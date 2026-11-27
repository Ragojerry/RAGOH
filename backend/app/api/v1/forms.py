import uuid

from fastapi import APIRouter, HTTPException, Response, UploadFile, status
from fastapi import Form as FormField
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import DB, ManagerOrAdmin, StaffUser
from app.db.models import Campaign, Form, FormKind, FormVersion, User
from app.schemas import FormVersionOut
from app.services.audit import audit
from app.services.forms import FormCompileError, compile_xlsform

router = APIRouter(prefix="/forms", tags=["forms"])

MAX_XLSFORM_BYTES = 5 * 1024 * 1024


def publish_form(
    db: Session,
    campaign: Campaign,
    kind: FormKind,
    content: bytes,
    filename: str,
    actor: User | None,
) -> tuple[Form, FormVersion, bool]:
    """Compile and store a form version. Returns (form, version, created_new_version)."""
    compiled = compile_xlsform(content, filename)
    form = db.scalar(
        select(Form).where(Form.campaign_id == campaign.id, Form.id_string == compiled.id_string)
    )
    if form is None:
        form = Form(
            campaign_id=campaign.id, id_string=compiled.id_string, name=compiled.title, kind=kind
        )
        db.add(form)
        db.flush()
    elif form.kind != kind:
        raise FormCompileError(
            f"Form '{compiled.id_string}' already exists as a {form.kind.value} form"
        )
    form.name = compiled.title
    existing = db.scalar(
        select(FormVersion).where(
            FormVersion.form_id == form.id, FormVersion.version == compiled.version
        )
    )
    if existing is not None:
        if existing.schema == compiled.schema:
            return form, existing, False
        raise FormCompileError(
            f"Version {compiled.version} already exists with different content — "
            "increase the version in the settings sheet"
        )
    version = FormVersion(
        form_id=form.id,
        version=compiled.version,
        schema=compiled.schema,
        xlsform=content,
        published_by=actor.id if actor else None,
    )
    db.add(version)
    db.flush()
    return form, version, True


def latest_versions(db: Session, campaign_ids: list[uuid.UUID], kinds: set[FormKind] | None = None):  # type: ignore[no-untyped-def]
    """(form, latest version) pairs for active forms in the given campaigns."""
    if not campaign_ids:
        return []
    stmt = (
        select(Form, FormVersion)
        .join(FormVersion, FormVersion.form_id == Form.id)
        .where(Form.campaign_id.in_(campaign_ids), Form.is_active.is_(True))
        .order_by(Form.id, FormVersion.published_at.desc(), FormVersion.version.desc())
        .distinct(Form.id)
    )
    if kinds:
        stmt = stmt.where(Form.kind.in_(kinds))
    return list(db.execute(stmt).tuples().all())


def version_out(form: Form, v: FormVersion, include_schema: bool) -> FormVersionOut:
    return FormVersionOut(
        form_id=form.id,
        version_id=v.id,
        campaign_id=form.campaign_id,
        id_string=form.id_string,
        name=form.name,
        kind=form.kind.value,
        version=v.version,
        published_at=v.published_at,
        schema=v.schema if include_schema else None,
    )


@router.post("", response_model=FormVersionOut, status_code=status.HTTP_201_CREATED)
async def upload_form(
    db: DB,
    user: ManagerOrAdmin,
    file: UploadFile,
    response: Response,
    campaign_id: uuid.UUID = FormField(...),
    kind: FormKind = FormField(FormKind.survey),
) -> FormVersionOut:
    """Upload an XLSForm (.xlsx). Re-uploading with a higher version publishes a new version."""
    campaign = db.get(Campaign, campaign_id)
    if campaign is None or campaign.organisation_id != user.organisation_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Campaign not found")
    content = await file.read(MAX_XLSFORM_BYTES + 1)
    if len(content) > MAX_XLSFORM_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Form file larger than 5 MB")
    try:
        form, version, created = publish_form(
            db, campaign, kind, content, file.filename or "form.xlsx", user
        )
    except FormCompileError as exc:
        raise HTTPException(422, str(exc)) from None
    if created:
        audit(
            db,
            "form_publish",
            actor_id=user.id,
            entity="form",
            entity_id=form.id,
            details={"version": version.version},
        )
    else:
        response.status_code = status.HTTP_200_OK
    db.commit()
    return version_out(form, version, include_schema=True)


@router.get("", response_model=list[FormVersionOut])
def list_forms(
    db: DB, user: StaffUser, campaign_id: uuid.UUID | None = None
) -> list[FormVersionOut]:
    stmt = select(Campaign.id).where(Campaign.organisation_id == user.organisation_id)
    if campaign_id:
        stmt = stmt.where(Campaign.id == campaign_id)
    return [
        version_out(f, v, include_schema=False)
        for f, v in latest_versions(db, list(db.scalars(stmt)))
    ]


@router.get("/versions/{version_id}/xlsform")
def download_xlsform(version_id: uuid.UUID, db: DB, user: StaffUser) -> Response:
    row = db.execute(
        select(Form, FormVersion)
        .join(FormVersion, FormVersion.form_id == Form.id)
        .join(Campaign, Campaign.id == Form.campaign_id)
        .where(FormVersion.id == version_id, Campaign.organisation_id == user.organisation_id)
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Form version not found")
    form, version = row
    return Response(
        version.xlsform,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{form.id_string}-{version.version}.xlsx"'
        },
    )

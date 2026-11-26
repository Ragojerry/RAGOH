"""Compile XLSForm (ODK/Kobo style) into RAGOH's JSON form schema.

pyxform parses the workbook; we then convert its tree into a compact schema that the
shared TypeScript form engine (packages/form-engine) renders on the phone. Anything
outside the supported subset is rejected with a clear message at upload time.
"""

import os
import tempfile
from dataclasses import dataclass
from typing import Any

from pyxform.errors import PyXFormError
from pyxform.xls2json import parse_file_to_json

SCHEMA_VERSION = 1

# pyxform type -> RAGOH field type
TYPE_MAP = {
    "text": "text",
    "integer": "integer",
    "int": "integer",
    "decimal": "decimal",
    "select one": "select_one",
    "select all that apply": "select_multiple",
    "select multiple": "select_multiple",
    "date": "date",
    "time": "time",
    "dateTime": "datetime",
    "geopoint": "geopoint",
    "image": "image",
    "photo": "image",
    "barcode": "barcode",
    "note": "note",
    "acknowledge": "acknowledge",
    "trigger": "acknowledge",
    "calculate": "calculate",
    "group": "group",
    "repeat": "repeat",
}
# Metadata the app records itself — accepted in the sheet but not shown as questions.
SILENT_TYPES = {"start", "end", "today", "deviceid", "imei", "phonenumber", "username", "audit", "simserial", "subscriberid", "email", "start-geopoint"}  # fmt: skip  # noqa: E501

BIND_KEYS = {
    "relevant": "relevant",
    "constraint": "constraint",
    "required": "required",
    "calculate": "calculation",
    "readonly": "read_only",
}


class FormCompileError(ValueError):
    pass


@dataclass
class CompiledForm:
    id_string: str
    version: str
    title: str
    schema: dict[str, Any]


def _text(value: Any, lang: str | None) -> str | None:
    """Labels may be plain strings or {language: text}."""
    if value is None:
        return None
    if isinstance(value, dict):
        if lang and lang in value:
            return str(value[lang])
        if "default" in value:
            return str(value["default"])
        if "English (en)" in value:
            return str(value["English (en)"])
        return str(next(iter(value.values()))) if value else None
    return str(value)


def _convert(
    node: dict[str, Any], lang: str | None, path: str, errors: list[str]
) -> dict[str, Any] | None:
    ptype = node.get("type", "")
    name = node.get("name", "")
    if name == "meta" and ptype == "group":
        return None
    if ptype in SILENT_TYPES:
        return None
    ftype = TYPE_MAP.get(ptype)
    if ftype is None:
        errors.append(f"'{name}': question type '{ptype}' is not supported yet")
        return None

    out: dict[str, Any] = {"type": ftype, "name": name}
    label = _text(node.get("label"), lang)
    if label:
        out["label"] = label
    hint = _text(node.get("hint"), lang)
    if hint:
        out["hint"] = hint
    bind = node.get("bind") or {}
    for src, dst in BIND_KEYS.items():
        if src in bind and bind[src] not in (None, ""):
            out[dst] = str(bind[src])
    msg = bind.get("jr:constraintMsg") or node.get("constraint_message")
    if msg:
        out["constraint_message"] = _text(msg, lang)
    req_msg = bind.get("jr:requiredMsg")
    if req_msg:
        out["required_message"] = _text(req_msg, lang)
    if node.get("default") not in (None, ""):
        out["default"] = str(node["default"])
    control = node.get("control") or {}
    appearance = control.get("appearance") or node.get("appearance")
    if appearance:
        out["appearance"] = str(appearance)
    if node.get("choice_filter"):
        out["choice_filter"] = str(node["choice_filter"])

    if ftype in ("select_one", "select_multiple"):
        choices = node.get("choices")
        if not choices:
            list_name = node.get("list_name") or node.get("itemset")
            errors.append(f"'{name}': choice list '{list_name}' is empty or missing")
        else:
            out["choices"] = [
                {
                    "name": str(c["name"]),
                    "label": _text(c.get("label"), lang) or str(c["name"]),
                    **{k: v for k, v in c.items() if k not in ("name", "label", "media")},
                }
                for c in choices
            ]
    if ftype in ("group", "repeat"):
        children = []
        for child in node.get("children", []):
            converted = _convert(child, lang, f"{path}/{name}", errors)
            if converted is not None:
                children.append(converted)
        out["children"] = children
        if ftype == "repeat" and node.get("control", {}).get("jr:count"):
            out["repeat_count"] = str(node["control"]["jr:count"])
    return out


def compile_xlsform(content: bytes, filename: str = "form.xlsx") -> CompiledForm:
    suffix = os.path.splitext(filename)[1].lower() or ".xlsx"
    if suffix not in (".xlsx", ".xls"):
        raise FormCompileError("Upload the form as an .xlsx file (XLSForm)")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        path = tmp.name
    try:
        try:
            tree = parse_file_to_json(path)
        except PyXFormError as exc:
            raise FormCompileError(f"XLSForm error: {exc}") from None
        except Exception as exc:  # pyxform raises various errors for malformed workbooks
            raise FormCompileError(f"Could not read the XLSForm: {exc}") from None
    finally:
        os.unlink(path)

    lang = tree.get("default_language")
    lang = None if lang in (None, "default") else lang
    errors: list[str] = []
    fields = []
    for child in tree.get("children", []):
        converted = _convert(child, lang, "", errors)
        if converted is not None:
            fields.append(converted)
    if errors:
        raise FormCompileError("Unsupported features: " + "; ".join(errors))
    if not fields:
        raise FormCompileError("The form has no questions")

    id_string = str(tree.get("id_string") or "").strip()
    if not id_string or id_string == "data":
        raise FormCompileError("Set form_id in the settings sheet")
    version = str(tree.get("version") or "1")
    title = str(tree.get("title") or id_string)
    schema = {
        "schema_version": SCHEMA_VERSION,
        "id_string": id_string,
        "version": version,
        "title": title,
        "fields": fields,
    }
    return CompiledForm(id_string=id_string, version=version, title=title, schema=schema)

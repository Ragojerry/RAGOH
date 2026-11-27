/**
 * Build-time additions to app.json.
 *
 * Push notifications on Android need Firebase (FCM) and an Expo project id. Both are
 * optional: without them the app builds and works, and supervisors get in-app alerts
 * while the app is open instead of push notifications. See docs/MOBILE.md → Push.
 *
 *   GOOGLE_SERVICES_JSON  path to google-services.json (CI writes it from a secret)
 *   EXPO_PROJECT_ID       the EAS project id used for Expo push tokens
 */
const fs = require("fs");

module.exports = ({ config }) => {
  const googleServices = process.env.GOOGLE_SERVICES_JSON;
  const projectId = process.env.EXPO_PROJECT_ID;
  return {
    ...config,
    android: {
      ...config.android,
      ...(googleServices && fs.existsSync(googleServices) ? { googleServicesFile: googleServices } : {}),
    },
    extra: {
      ...config.extra,
      ...(projectId ? { eas: { projectId } } : {}),
    },
  };
};

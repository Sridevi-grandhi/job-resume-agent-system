/** Shared authenticated display name for header and sidebar. */

export function userDisplayName(user, { genericFallback = "Career seeker" } = {}) {
  const storedName = String(user?.name || "").trim();
  if (storedName) return storedName;
  const emailPrefix = String(user?.email || "").split("@")[0].trim();
  if (emailPrefix) return emailPrefix;
  return genericFallback;
}

export function userInitial(user) {
  const display = userDisplayName(user);
  return String(display).charAt(0).toUpperCase() || "U";
}

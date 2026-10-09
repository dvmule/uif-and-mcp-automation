// authorship.js (UIFPE-611)
// Who made an export and who last changed it, as the export page's Identity
// card shows them. The API stamps both from the verified login: `author` is
// the creator (carried unchanged to later versions), `published_by` is who
// published each version.

/** The latest version's publisher when it is worth a "Modified by" line:
 *  someone has edited the export (a later version), or the publisher is not
 *  the author. Versions from before published_by existed carry no record of
 *  it, so they show nothing rather than a guess. */
export function modifiedBy(version) {
  const by = version?.published_by;
  if (!by) return null;
  return version.version > 1 || by !== version.author ? by : null;
}

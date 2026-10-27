# Manual plate confirmation and vehicle colors

Press **P** or **P 確認車牌** in the lower-right controls to review a frozen image of the latest vehicle. Monitoring and recording continue. Select the correct vehicle when several are present, enter its correct plate, choose a body color (or unknown), set tolerance to 1–3 characters (default 3), and save.

Unconfirmed OCR candidates and available plate crops appear in the lower-left card. A saved confirmation updates that card only while the same vehicle group remains; it cannot relabel a newly arrived car. Manual saving does not replay old videos or send a retrospective Telegram notification.

Subsequent live OCR gives priority to human-confirmed database plates within the configured edit distance, including substitutions, missing characters and extra characters. At least four observed alphanumeric characters are required. Reliable color from the uniquely associated vehicle can reject conflicting candidates or break a tie. Low-light/near-monochrome frames and overlapping vehicles supply no reliable color; white, silver and gray are treated as a related group.

A unique candidate still requires at least three independent, spatially consistent frames. Ambiguous candidates remain pending. Database matches are inferences from reviewed records, not proof that OCR read every character: the GUI marks database matches and Telegram retains the original reading. Different misreadings of one database plate share its three-minute notification cooldown. Inferred matches never become new automatic learning anchors.

The review window lists stored plates, colors and enabled states. Disable an incorrect entry; confirming the same plate again re-enables it or updates its color. Database tolerance is separate from the existing two-character highlight-list tolerance.

The private SQLite database is `config/vehicle_registry.sqlite3`, with transactional records and a manual-confirmation audit trail. Confirmed entries persist beyond the seven-day recognition history. Frozen review images in `runtime/plate_reviews` expire after seven days. Close review windows before updating. Database files, WAL/SHM sidecars and real images are excluded from publication; public illustrations are synthetic.

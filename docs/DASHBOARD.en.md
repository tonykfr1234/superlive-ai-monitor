# Four-panel dashboard

![Four-panel diagram rendered by the actual application using a synthetic scene](media/dashboard-guide.en.png)

All vehicles, plates and events shown are synthetic. This is an interface demonstration, not an accuracy benchmark.

| Default position | Contents | Interaction |
| --- | --- | --- |
| Top left | Full camera image, polygon, pulsing recording border | R to select polygon vertices inside the real image; Enter to save |
| Top right | Seven-day plate history and collapsible other records | Wheel, PgUp/PgDn or scrollbar; click a video filename to reveal it in Explorer |
| Bottom left | Latest vehicle time, associated plate and captured image | The photo has its own capture time; simultaneous vehicles share a group crop |
| Bottom right | All controls and monitoring status | Region, full frame, save, cancel, other records, stop and reset layout |

Drag any card header onto another card to swap their positions. Escape cancels an active drag. Press **L** or the reset-layout button to restore the default positions. Ordering is kept locally in `runtime/preview_layout.json`.

![Synthetic animation showing real drag-and-swap interaction and layout reset](media/dashboard-layout.gif)

Cards resize with the window. The minimum canvas is 640 × 720; 1280 × 820 or larger is recommended. Camera images retain their full aspect ratio. A soft backdrop fills unused space without stretching or cropping the actual camera image. Backdrop pixels are excluded from polygon selection. UI effects never modify recorded evidence.

The latest vehicle-group crop updates at most once per second, fits within 960 × 540, and stays visible after the vehicle leaves. Recording resolution is unaffected. The local JPEG is bound to its observation JSON by a hash; mismatched photos are not restored after restart. New vehicle groups cannot inherit the previous group's image or plate. Local photos and private configuration must never be committed to GitHub.

Immediate plate notifications and the independent chronological video queue retain their existing rules: one notification per normalized observed plate per 180 seconds, independent cooldowns for different plates, no historical video scanning. Moving and stationary vehicles can both be recognized from fresh live frames.

Apply a prepared local update using `套用Telegram更新.bat` in the installation directory; it includes this dashboard update and preserves local settings and recordings. See the [installation guide](https://github.com/tonykfr1234/superlive-ai-monitor#quick-start) for a fresh install.

Regenerate these assets with `python scripts/generate_dashboard_demo.py`. The generator uses synthetic pixels and the actual renderer without camera access, model inference or Telegram calls.


The latest vehicle card now places the vehicle on the left and its verified evidence-frame plate crop and text on the right. See [one-click installation and local corrections](INSTALL.en.md).


Press P in the dashboard to manually confirm a plate and vehicle color. Subsequent live recognition prioritizes the reviewed local database with configurable 1–3-character tolerance and explicit inference labels. See [manual confirmation and vehicle database](VEHICLE_DATABASE.en.md).

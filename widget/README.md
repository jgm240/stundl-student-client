# SSC widget (WidgetKit)

1. `cd widget && xcodegen`, open `SSCWidget.xcodeproj`, set your Team on both targets, run the host app once.
2. Right-click the desktop -> Edit Widgets -> SSC.
3. `python ssc.py widget` (and every `sync` from then on) writes `widget.json` into the widget's own
   container: `~/Library/Containers/com.stundl.ssc.host.widget/Data/Library/Application Support/`.
   No App Group or ID needed. macOS may ask once whether Python may access data from other apps - allow it.

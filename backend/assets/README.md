The PDF renderer embeds `manrope-report.ttf`, a static weight-450 instance of
`frontend/public/fonts/manrope-variable.ttf`. It was generated with FontTools
`varLib.instancer.instantiateVariableFont(font, {"wght": 450}, inplace=True)`.
The adjacent OFL license covers both versions. FontTools is an authoring tool;
the application requires only the committed static font and ReportLab.

The app-like PDF layout also embeds a static weight-700 Manrope instance for
metrics and headings. `kharcha-report-icon.png` is the unchanged app logo
resampled from the frontend artwork to 156 pixels for crisp, compact print
embedding. Report colors are read directly from the app light-theme tokens.

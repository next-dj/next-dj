const chartDefaults = window.Chart && window.Chart.defaults;

if (chartDefaults) {
  const tokens = getComputedStyle(document.documentElement);
  const hsl = function (name) {
    return "hsl(" + tokens.getPropertyValue(name).trim() + ")";
  };

  chartDefaults.font.family = getComputedStyle(document.body).fontFamily;
  chartDefaults.font.size = 12;
  chartDefaults.color = hsl("--muted-foreground");
  chartDefaults.borderColor = hsl("--border");
}

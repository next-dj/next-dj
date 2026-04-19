(function () {
  document.addEventListener("DOMContentLoaded", function () {
    var detail = document.getElementById("widget-details");
    if (!detail) return;
    detail.addEventListener("shown.bs.collapse", function () {
      console.log("[next-dj static example] widget details shown");
    });
  });
})();

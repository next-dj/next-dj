(function () {
    function initChart(canvas) {
        if (typeof window.Chart !== "function") return;
        var labels = (canvas.dataset.chartLabels || "").split(",").filter(Boolean);
        var values = (canvas.dataset.chartValues || "")
            .split(",")
            .filter(Boolean)
            .map(Number);
        new window.Chart(canvas, {
            type: "bar",
            data: {
                labels: labels,
                datasets: [
                    {
                        label: "Visits",
                        data: values,
                        backgroundColor: "rgba(13, 110, 253, 0.6)",
                        borderColor: "rgba(13, 110, 253, 1)",
                        borderWidth: 1,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: { y: { beginAtZero: true } },
            },
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll(".chart-canvas").forEach(initChart);
    });
})();

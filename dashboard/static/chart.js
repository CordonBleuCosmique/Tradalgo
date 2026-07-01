// Minimal vanilla-canvas cumulative PnL line chart. No CDN / JS package
// dependency on purpose -- keeps the dashboard's static assets self
// contained and offline-buildable.
(function () {
    const canvas = document.getElementById("pnl-chart");
    if (!canvas || typeof pnlData === "undefined" || pnlData.length === 0) {
        return;
    }
    const ctx = canvas.getContext("2d");
    const width = canvas.width;
    const height = canvas.height;
    const padding = 30;

    const values = pnlData.map((p) => p.cumulative_pnl);
    const minVal = Math.min(0, ...values);
    const maxVal = Math.max(0, ...values);
    const range = maxVal - minVal || 1;

    function xFor(i) {
        return padding + (i / Math.max(pnlData.length - 1, 1)) * (width - 2 * padding);
    }
    function yFor(v) {
        return height - padding - ((v - minVal) / range) * (height - 2 * padding);
    }

    ctx.strokeStyle = "#2a2d34";
    ctx.beginPath();
    ctx.moveTo(padding, yFor(0));
    ctx.lineTo(width - padding, yFor(0));
    ctx.stroke();

    ctx.strokeStyle = "#7aa2f7";
    ctx.lineWidth = 2;
    ctx.beginPath();
    pnlData.forEach((point, i) => {
        const x = xFor(i);
        const y = yFor(point.cumulative_pnl);
        if (i === 0) {
            ctx.moveTo(x, y);
        } else {
            ctx.lineTo(x, y);
        }
    });
    ctx.stroke();
})();

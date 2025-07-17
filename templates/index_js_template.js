const spots = $spots_json;

var map = L.map('map').setView([$center_lat, $center_lon], 11);

L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap contributors'
}).addTo(map);

function createIcon(risk) {
    return L.divIcon({
        html: '<div class="marker-icon marker-' + risk.toLowerCase() + '"></div>',
        iconSize: [20, 20],
        iconAnchor: [10, 10],
        className: ''
    });
}

spots.forEach(function(s) {
    var marker = L.marker([s.lat, s.lon], {icon: createIcon(s.risk)}).addTo(map);
    marker.on('click', function() { window.location.href = s.link; });
    marker.bindTooltip(s.label);
});

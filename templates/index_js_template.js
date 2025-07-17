// Leaflet map initialization
const sites = $sites_json;

const map = L.map('map').setView([$center_lat, $center_lon], 10);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap contributors'
}).addTo(map);

sites.forEach(site => {
    const color = site.risk === 'High' ? 'red' : site.risk === 'Medium' ? 'orange' : 'green';
    const marker = L.circleMarker([site.lat, site.lon], {
        radius: 8,
        color: color,
        fillColor: color,
        fillOpacity: 0.8
    }).addTo(map);
    marker.bindTooltip(site.name);
    marker.on('click', () => {
        window.location.href = site.link;
    });
});

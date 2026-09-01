// Leaflet map initialization
const sites = [{"name": "Avon at Conham River", "lat": 51.444858, "lon": -2.534812, "risk": "Medium", "link": "conham.html"}, {"name": "Avon at Salford", "lat": 51.398639, "lon": -2.446917, "risk": "Low", "link": "salford.html"}, {"name": "Avon at Warleigh Weir", "lat": 51.376556, "lon": -2.301611, "risk": "Medium", "link": "warleigh.html"}, {"name": "River Chew at Publow", "lat": 51.375278, "lon": -2.543306, "risk": "Low", "link": "chew.html"}, {"name": "River Frome at Farleigh Hungerford", "lat": 51.3299, "lon": -2.288, "risk": "Medium", "link": "farleigh.html"}];

const map = L.map('map').setView([51.3850462, -2.4229291999999996], 10);
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

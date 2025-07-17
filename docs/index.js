const sites = [
    {name: 'Avon at Conham River', lat: 51.444858, lon: -2.534812, risk: 'Medium', link: 'conham.html'},
    {name: 'Avon at Salford', lat: 51.444858, lon: -2.534812, risk: 'Medium', link: 'salford.html'},
    {name: 'Avon at Warleigh Weir', lat: 51.444858, lon: -2.534812, risk: 'Low', link: 'warleigh.html'},
    {name: 'River Chew at Publow', lat: 51.415847, lon: -2.497921, risk: 'Medium', link: 'chew.html'}
];
const map = L.map('map').setView([51.445, -2.516], 10);
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

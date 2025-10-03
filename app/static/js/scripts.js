function sendAction(action) {
    fetch(`/control/${action}`, { method: 'POST' })
        .then(response => response.json())
        .then(data => console.log(data))
        .catch(err => console.error(err));
}

// Background image load detection
(function() {
    const backgroundContainer = document.getElementById('backgroundContainer');

    // Extract background image URL
    let bgImageUrl = window.getComputedStyle(backgroundContainer).backgroundImage;
    const urlMatch = bgImageUrl && bgImageUrl.match(/url\(["']?(.*?)["']?\)/);
    
    if (!urlMatch || !urlMatch[1]) {
        console.warn('No valid background image URL found');
        return;
    }
    
    bgImageUrl = urlMatch[1];
    
    const bgImage = new Image();
    bgImage.onerror = function() {
        console.error('Background image load failed:', bgImageUrl);
    };

    // Show model loading on background load success
    bgImage.onload = function() {
        modelLoading.style.display = 'flex';
    };

    bgImage.src = bgImageUrl;
})();
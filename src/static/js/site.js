(function () {
  var y = document.getElementById('year');
  if (y) y.textContent = new Date().getFullYear();

  // Mobile nav
  var toggle = document.getElementById('navToggle');
  var nav = document.getElementById('siteNav');
  if (toggle && nav) {
    toggle.addEventListener('click', function () {
      var open = nav.classList.toggle('is-open');
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
  }

  // News ticker: rotate one headline at a time, pause on hover
  var track = document.querySelector('[data-ticker]');
  if (track) {
    var items = track.querySelectorAll('.mh-ticker-item');
    var i = 0, paused = false;
    if (items.length > 1 && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      track.addEventListener('mouseenter', function () { paused = true; });
      track.addEventListener('mouseleave', function () { paused = false; });
      setInterval(function () {
        if (paused) return;
        items[i].classList.remove('is-visible');
        i = (i + 1) % items.length;
        items[i].classList.add('is-visible');
      }, 5000);
    }
  }

  // Newsletter forms via fetch (progressive enhancement)
  document.querySelectorAll('[data-subscribe]').forEach(function (form) {
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var msg = form.querySelector('.subscribe-form__msg');
      var btn = form.querySelector('button');
      var email = form.querySelector('input[type=email]').value.trim();
      btn.disabled = true;
      fetch(form.action, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify({ email: email, source: form.querySelector('[name=source]').value, website: form.querySelector('[name=website]').value })
      }).then(function (r) { return r.json(); }).then(function (j) {
        if (j.ok) {
          form.classList.add('is-done');
          msg.textContent = 'You’re on the list. Watch your inbox tomorrow morning.';
        } else {
          msg.textContent = j.error || 'Something went wrong. Please try again.';
        }
      }).catch(function () { msg.textContent = 'Network error. Please try again.'; })
        .finally(function () { btn.disabled = false; });
    });
  });

  // Copy link
  document.querySelectorAll('[data-copy]').forEach(function (b) {
    b.addEventListener('click', function () {
      var url = location.href.split('?')[0];
      if (navigator.share) { navigator.share({ title: document.title, url: url }).catch(function () {}); return; }
      navigator.clipboard.writeText(url).then(function () { b.textContent = '✓'; setTimeout(function () { b.textContent = '🔗'; }, 1500); });
    });
  });

  // Hide broken hotlinked images gracefully
  document.querySelectorAll('img').forEach(function (img) {
    img.addEventListener('error', function () {
      var box = img.closest('.card__media, .hero__media, .entry-thumbnail');
      if (box) box.classList.add('is-broken');
    });
  });
})();

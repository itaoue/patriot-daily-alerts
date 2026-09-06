(function () {
  // Date + year
  var d = document.getElementById('todayDate');
  if (d) d.textContent = new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
  var y = document.getElementById('year');
  if (y) y.textContent = new Date().getFullYear();

  // Mobile nav
  var toggle = document.getElementById('navToggle');
  var nav = document.getElementById('siteNav');
  if (toggle && nav) {
    toggle.addEventListener('click', function () {
      var open = nav.classList.toggle('is-open');
      toggle.classList.toggle('is-open', open);
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      document.body.classList.toggle('nav-open', open);
    });
  }

  // Compact header on scroll
  var head = document.getElementById('masthead');
  var last = 0;
  window.addEventListener('scroll', function () {
    var s = window.scrollY;
    if (head) head.classList.toggle('is-compact', s > 140);
    var p = document.getElementById('readProgress');
    if (p) {
      var h = document.documentElement;
      var total = h.scrollHeight - h.clientHeight;
      p.style.width = (total > 0 ? Math.min(100, (s / total) * 100) : 0) + '%';
    }
    last = s;
  }, { passive: true });

  // Ticker: duplicate items so the marquee loops seamlessly
  var track = document.querySelector('[data-ticker]');
  if (track && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    var items = track.querySelector('.ticker__items');
    var clone = items.cloneNode(true);
    clone.setAttribute('aria-hidden', 'true');
    track.appendChild(clone);
    track.classList.add('is-animated');
    var speed = Math.max(25, items.scrollWidth / 60);
    track.style.setProperty('--ticker-duration', speed + 's');
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

  // Make any hotlinked images that fail load gracefully
  document.querySelectorAll('img').forEach(function (img) {
    img.addEventListener('error', function () { img.closest('.card__media, .hero__media, .article__hero') && img.closest('.card__media, .hero__media, .article__hero').classList.add('is-broken'); });
  });
})();

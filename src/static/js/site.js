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
      var payload = { email: email };  // every field, so the landing page's UTM + next fields travel too
      new FormData(form).forEach(function (v, k) { if (k !== 'email') payload[k] = v; });
      fetch(form.action, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify(payload)
      }).then(function (r) { return r.json(); }).then(function (j) {
        if (j.ok) {
          if (typeof gtag === 'function') gtag('event', 'newsletter_signup', { source: payload.source || 'site' });
          if (j.next) { location.href = j.next; return; }
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
      var url = b.getAttribute('data-copy-url') || location.href.split('?')[0];
      var label = b.textContent;
      if (navigator.share) { navigator.share({ title: document.title, url: url }).catch(function () {}); return; }
      navigator.clipboard.writeText(url).then(function () { b.textContent = label.length > 2 ? 'Copied ✓' : '✓'; setTimeout(function () { b.textContent = label; }, 1500); });
    });
  });

  // Landing page: the sticky mobile CTA only shows once the hero signup form has scrolled out of view
  var sticky = document.querySelector('.lp-sticky');
  var heroForm = document.getElementById('form-hero');
  if (sticky && heroForm && 'IntersectionObserver' in window) {
    sticky.classList.add('is-hidden');
    new IntersectionObserver(function (entries) {
      sticky.classList.toggle('is-hidden', entries[0].isIntersecting);
    }, { threshold: 0.2 }).observe(heroForm);
  }

  // Hide broken hotlinked images gracefully
  document.querySelectorAll('img').forEach(function (img) {
    img.addEventListener('error', function () {
      var box = img.closest('.card__media, .hero__media, .entry-thumbnail');
      if (box) box.classList.add('is-broken');
    });
  });
})();

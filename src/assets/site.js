/* SofIA website behaviour. Pages are prerendered per language by tools/build.py; this script only adds
   interaction (carousels, tabs, audience picker, copy), the Portuguese variant switch, the language
   suggestion on the Spanish root and the deep-link router of 404.html. Everything it needs arrives in
   window.SOFIA, written by the build. */
(function () {
  var S = window.SOFIA || {};
  var html = document.documentElement;

  function store(key, value) { try { if (value === undefined) return localStorage.getItem(key); localStorage.setItem(key, value); } catch (e) { return null; } }

  /* Text of a rendered string, entities decoded (for aria-label and the title). */
  function plain(v) { var t = document.createElement('textarea'); t.innerHTML = v.replace(/<[^>]+>/g, ''); return t.value; }

  /* Replace every translatable node with the strings of `dict` (already rendered by the build). */
  function applyDict(dict) {
    document.querySelectorAll('[data-i18n]').forEach(function (el) { var v = dict[el.dataset.i18n]; if (v != null) el.innerHTML = v; });
    document.querySelectorAll('[data-i18n-aria]').forEach(function (el) { var v = dict[el.dataset.i18nAria]; if (v != null) el.setAttribute('aria-label', plain(v)); });
    document.querySelectorAll('[data-i18n-title]').forEach(function (el) { var v = dict[el.dataset.i18nTitle]; if (v != null) document.title = plain(v); });
  }

  /* ---------- Portuguese: /pt/ is prerendered in pt-BR; the head script picked the variant ---------- */
  try {
    if (S.variant && html.lang === S.variant.lang) { applyDict(S.variant.dict); S.aud = S.variant.aud; }
  } finally {
    html.classList.remove('pt-swap');
  }

  /* ---------- language suggestion on the Spanish root ---------- */
  if (S.suggest) {
    var box = document.getElementById('suggest');
    var dismissed = store('sofia-suggest') === 'no';
    var wanted = (navigator.languages && navigator.languages.length ? navigator.languages : [navigator.language || 'es']);
    var pick = null;
    for (var i = 0; i < wanted.length && !pick; i++) {
      var code = String(wanted[i]).toLowerCase().slice(0, 2);
      if (code === 'es') break;
      if (S.suggest[code]) pick = S.suggest[code];
    }
    if (box && pick && !dismissed) {
      box.querySelector('p').textContent = pick.text;
      box.setAttribute('lang', pick.lang);
      var go = box.querySelector('a'); go.textContent = pick.go; go.href = pick.href;
      var x = box.querySelector('.x'); x.setAttribute('aria-label', pick.close); x.title = pick.close;
      x.addEventListener('click', function () { box.hidden = true; store('sofia-suggest', 'no'); });
      box.hidden = false;
    }
  }

  /* ---------- 404: language by browser, then the deep-link router ---------- */
  if (S.router) {
    var langs = (navigator.languages && navigator.languages.length ? navigator.languages : [navigator.language || 'es']);
    var chosen = null;
    for (var j = 0; j < langs.length && !chosen; j++) {
      var l = String(langs[j]);
      if (/^pt-pt/i.test(l) && S.dicts['pt-PT']) chosen = 'pt-PT';
      else if (S.dicts[l.slice(0, 2).toLowerCase()]) chosen = l.slice(0, 2).toLowerCase();
    }
    if (chosen && chosen !== 'es') { applyDict(S.dicts[chosen]); html.lang = S.langTags[chosen] || chosen; S.aud = null; }

    /* Same rule as DeeplinkBusiness.resolve: empty segments dropped, first matching route wins. A
       webless route, or a route without its required parameters, is a link nobody can share. */
    var path = location.pathname.replace(/\/{2,}/g, '/').replace(/\/+$/, '');
    var params = new URLSearchParams(location.search);
    var route = null;
    for (var k = 0; k < S.router.length && !route; k++) {
      var m = new RegExp(S.router[k].re).exec(path);
      if (m) route = { r: S.router[k], m: m };
    }
    var known = !!route && route.r.web && route.r.requires.every(function (name) { return (route.m.groups && route.m.groups[name]) || params.get(name); });
    if (known) {
      var action = route.r.action;
      if (action === 'user') { document.getElementById('shared-title').innerHTML = document.getElementById('t-profile').innerHTML; }
      if (action !== 'entry' && action !== 'user') {
        document.getElementById('shared-title').innerHTML = document.getElementById('t-open').innerHTML;
        document.getElementById('shared-sub').innerHTML = document.getElementById('t-opensub').innerHTML;
      }
      if (action !== 'entry') {
        document.getElementById('shared-preview').hidden = true;
        document.getElementById('hint-mobile').innerHTML = document.getElementById('t-hint').innerHTML;
      }
      document.getElementById('shared').hidden = false;
      document.getElementById('notfound').hidden = true;
      var ua = navigator.userAgent;
      var android = /Android/i.test(ua);
      var ios = /iPhone|iPad|iPod/i.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
      var deep = path.slice(1) + location.search;  /* sofia://<path>?<query>: the app reads the same paths */
      var open = document.getElementById('open-app');
      if (android) {
        /* With `package` and no fallback, Chrome sends whoever lacks the app to its Play listing. */
        open.href = 'intent://' + deep + '#Intent;scheme=sofia;package=' + S.androidPackage + ';end';
        open.hidden = false;
      } else if (!ios) {
        document.getElementById('hint-mobile').hidden = true;
        document.getElementById('hint-desktop').hidden = false;
      }
      document.title = (document.querySelector('#shared h1') || {}).textContent || document.title;
    } else {
      document.title = ((document.querySelector('#notfound h1') || {}).textContent || '') + ' · SofIA';
    }
  }

  /* ---------- carousels ---------- */
  function cards(track) { return Array.prototype.slice.call(track.children); }
  function step(track) { var c = cards(track); return c.length > 1 ? c[1].offsetLeft - c[0].offsetLeft : track.clientWidth; }
  function update(track) {
    var max = track.scrollWidth - track.clientWidth - 2;
    document.querySelectorAll('.ctrl[data-for="' + track.id + '"] button').forEach(function (b) {
      b.disabled = (+b.dataset.dir < 0 && track.scrollLeft <= 2) || (+b.dataset.dir > 0 && track.scrollLeft >= max);
    });
    var dots = document.querySelector('.dots[data-for="' + track.id + '"]');
    if (!dots || !track.clientWidth) return;
    var s = Math.max(1, step(track));
    var visible = Math.max(1, Math.round(track.clientWidth / s));
    var pages = Math.max(1, cards(track).length - visible + 1);
    if (dots.children.length !== pages) {
      dots.innerHTML = '';
      for (var i = 0; i < pages; i++) {
        var d = document.createElement('button'); d.type = 'button'; d.setAttribute('aria-label', (S.words ? S.words.card : 'Ir a la tarjeta') + ' ' + (i + 1));
        (function (n) { d.addEventListener('click', function () { track.scrollTo({ left: n * step(track), behavior: 'smooth' }); }); })(i);
        dots.appendChild(d);
      }
    }
    var at = Math.min(pages - 1, Math.round(track.scrollLeft / s));
    if (track.scrollLeft >= max) at = pages - 1;
    Array.prototype.forEach.call(dots.children, function (d, i) { d.setAttribute('aria-current', i === at ? 'true' : 'false'); });
  }
  document.querySelectorAll('.track').forEach(function (track) {
    if (S.words) track.setAttribute('aria-roledescription', S.words.carousel);
    var raf; track.addEventListener('scroll', function () { cancelAnimationFrame(raf); raf = requestAnimationFrame(function () { update(track); }); }, { passive: true });
    track.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') { e.preventDefault(); track.scrollBy({ left: (e.key === 'ArrowRight' ? 1 : -1) * step(track), behavior: 'smooth' }); }
    });
    var down = false, sx = 0, sl = 0, moved = false;
    track.addEventListener('pointerdown', function (e) { if (e.pointerType !== 'mouse') return; down = true; moved = false; sx = e.clientX; sl = track.scrollLeft; track.style.scrollSnapType = 'none'; });
    window.addEventListener('pointermove', function (e) { if (!down) return; var dx = e.clientX - sx; if (Math.abs(dx) > 4) moved = true; track.scrollLeft = sl - dx; });
    window.addEventListener('pointerup', function () { if (!down) return; down = false; track.style.scrollSnapType = ''; var s = step(track); track.scrollTo({ left: Math.round(track.scrollLeft / s) * s, behavior: 'smooth' }); });
    track.addEventListener('click', function (e) { if (moved) { e.preventDefault(); e.stopPropagation(); } }, true);
    update(track);
  });
  document.querySelectorAll('.ctrl button').forEach(function (b) {
    b.addEventListener('click', function () { var tr = document.getElementById(b.parentNode.dataset.for); tr.scrollBy({ left: +b.dataset.dir * step(tr), behavior: 'smooth' }); });
  });
  window.addEventListener('resize', function () { document.querySelectorAll('.track').forEach(update); });

  /* ---------- tabs: write / talk / remember ---------- */
  var tabs = Array.prototype.slice.call(document.querySelectorAll('.seg [role="tab"]'));
  function select(btn) {
    tabs.forEach(function (b) {
      var on = b === btn; b.setAttribute('aria-selected', on); b.tabIndex = on ? 0 : -1;
      document.getElementById(b.getAttribute('aria-controls')).hidden = !on;
    });
  }
  tabs.forEach(function (b, i) {
    b.addEventListener('click', function () { select(b); });
    b.addEventListener('keydown', function (e) {
      var d = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : 0;
      if (!d) return; e.preventDefault(); var n = tabs[(i + d + tabs.length) % tabs.length]; select(n); n.focus();
    });
  });

  /* ---------- audience picker ---------- */
  var chip = document.getElementById('aud-chip');
  if (chip && S.aud) {
    var ICON = ['i-lock', 'i-heart-users', 'i-users', 'i-world'], PEOPLE = [1, 3, 4, 4];
    var av = document.getElementById('aud-avatars'), txt = document.getElementById('aud-text');
    document.querySelectorAll('[data-aud]').forEach(function (b) {
      b.addEventListener('click', function () {
        var n = +b.dataset.aud, a = S.aud[n];
        document.querySelectorAll('[data-aud]').forEach(function (x) { x.setAttribute('aria-pressed', x === b); });
        chip.querySelector('use').setAttribute('href', '#' + ICON[n]);
        chip.querySelector('span').innerHTML = a.chip;
        av.innerHTML = new Array(PEOPLE[n] + 1).join('<i></i>') + '<em>' + a.who + '</em>';
        txt.innerHTML = a.text;
      });
    });
  }

  /* ---------- copy ---------- */
  document.querySelectorAll('[data-copy]').forEach(function (b) {
    b.addEventListener('click', function () {
      var el = document.getElementById(b.getAttribute('data-copy')), label = b.innerHTML;
      var done = function () { b.textContent = '✓'; setTimeout(function () { b.innerHTML = label; }, 1600); };
      var sel = function () { var r = document.createRange(); r.selectNodeContents(el); var s = getSelection(); s.removeAllRanges(); s.addRange(r); };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(el.textContent).then(done, sel); else sel();
    });
  });

  /* ---------- menus close when a link inside is followed ---------- */
  document.querySelectorAll('details.menu a, details.lang a').forEach(function (a) {
    a.addEventListener('click', function () { var d = a.closest('details'); if (d) d.open = false; });
  });
})();

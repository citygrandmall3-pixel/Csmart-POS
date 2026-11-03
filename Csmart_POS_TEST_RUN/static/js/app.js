/* أدوات مشتركة */
'use strict';

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

function fmt(n) {
  return Number(n || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtDate(iso) {
  if (!iso) return '';
  const [y, m, d] = String(iso).slice(0, 10).split('-');
  return d ? `${d}/${m}/${y}` : iso;
}

async function api(url, opts = {}) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
    body: opts.body ? (typeof opts.body === "string" ? opts.body : JSON.stringify(opts.body)) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401) { location.href = '/login'; throw new Error(data.error || 'انتهت الجلسة'); }
  if (!res.ok) throw new Error(data.error || 'حدث خطأ غير متوقع');
  return data;
}

function toast(msg, type = 'ok') {
  let box = $('#toasts');
  if (!box) { box = document.createElement('div'); box.id = 'toasts'; document.body.appendChild(box); }
  const t = document.createElement('div');
  t.className = `toast ${type}`;
  t.innerHTML = `<span class="ticon">${type === 'ok' ? '✔' : '✖'}</span><span>${esc(msg)}</span>`;
  box.appendChild(t);
  setTimeout(() => { t.style.opacity = '0'; t.style.transition = 'opacity .4s'; setTimeout(() => t.remove(), 400); }, 4200);
}

function openModal(id) { $('#' + id).classList.add('open'); }
function closeModal(id) { $('#' + id).classList.remove('open'); }

document.addEventListener('click', e => {
  const ov = e.target.closest('.modal-overlay');
  if (ov && e.target === ov) ov.classList.remove('open');
  if (e.target.closest('[data-close-modal]')) e.target.closest('.modal-overlay').classList.remove('open');
});

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') $$('.modal-overlay.open').forEach(m => m.classList.remove('open'));
});

async function confirmDlg(msg) {
  return window.confirm(msg);
}

/* ============================================================
   STEP 3.2 — Sidebar + Mobile Zoom
   ============================================================ */

(() => {
  const sidebar = document.getElementById('main-sidebar');
  const toggle = document.getElementById('sidebar-toggle');
  const overlay = document.getElementById('sidebar-overlay');

  if (sidebar && toggle && overlay) {
    const closeSidebar = () => {
      sidebar.classList.remove('open');
      overlay.classList.remove('open');
      toggle.setAttribute('aria-expanded', 'false');
    };

    const openSidebar = () => {
      sidebar.classList.add('open');
      overlay.classList.add('open');
      toggle.setAttribute('aria-expanded', 'true');
    };

    toggle.addEventListener('click', () => {
      if (sidebar.classList.contains('open')) {
        closeSidebar();
      } else {
        openSidebar();
      }
    });

    overlay.addEventListener('click', closeSidebar);

    sidebar.querySelectorAll('a').forEach(a => {
      a.addEventListener('click', closeSidebar);
    });

    window.addEventListener('resize', () => {
      if (window.innerWidth > 900) {
        closeSidebar();
      }
    });
  }

  /* ----------------------------------------------------------
     Zoom: 80% إلى 120%
     ---------------------------------------------------------- */

  const zoomOut = document.getElementById('zoom-out');
  const zoomIn = document.getElementById('zoom-in');
  const zoomReset = document.getElementById('zoom-reset');

  let zoom = Number(localStorage.getItem('accounting_zoom') || '100');

  const applyZoom = () => {
    zoom = Math.max(80, Math.min(120, zoom));
    document.documentElement.style.zoom = `${zoom}%`;

    if (zoomReset) {
      zoomReset.textContent = `${zoom}%`;
    }

    localStorage.setItem('accounting_zoom', String(zoom));
  };

  if (zoomOut) {
    zoomOut.addEventListener('click', () => {
      zoom -= 10;
      applyZoom();
    });
  }

  if (zoomIn) {
    zoomIn.addEventListener('click', () => {
      zoom += 10;
      applyZoom();
    });
  }

  if (zoomReset) {
    zoomReset.addEventListener('click', () => {
      zoom = 100;
      applyZoom();
    });
  }

  applyZoom();
})();


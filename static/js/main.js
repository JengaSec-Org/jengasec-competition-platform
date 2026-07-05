/* JengaSec Evaluation Platform — shared UI behaviour (vanilla JS). */
'use strict';

document.addEventListener('DOMContentLoaded', () => {
  // Mobile sidebar toggle
  const toggle = document.getElementById('nav-toggle');
  const sidebar = document.getElementById('app-sidebar');
  if (toggle && sidebar) {
    toggle.addEventListener('click', () => sidebar.classList.toggle('open'));
    document.addEventListener('click', (e) => {
      if (!sidebar.contains(e.target) && !toggle.contains(e.target)) {
        sidebar.classList.remove('open');
      }
    });
  }

  // Mark the active sidebar link from the current path
  const path = window.location.pathname;
  document.querySelectorAll('.side-link[href]').forEach((link) => {
    const href = link.getAttribute('href');
    if (href !== '#' && href !== '/' && path.startsWith(href)) {
      link.classList.add('active');
    } else if (href === '/' && path === '/') {
      link.classList.add('active');
    }
  });

  // Dismissible alerts
  document.querySelectorAll('.alert .alert-close').forEach((btn) => {
    btn.addEventListener('click', () => btn.closest('.alert').remove());
  });

  // Dropdowns
  document.querySelectorAll('.dropdown > [data-dropdown-toggle]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const dd = btn.closest('.dropdown');
      document.querySelectorAll('.dropdown.open').forEach((other) => {
        if (other !== dd) other.classList.remove('open');
      });
      dd.classList.toggle('open');
    });
  });
  document.addEventListener('click', () => {
    document.querySelectorAll('.dropdown.open').forEach((dd) => dd.classList.remove('open'));
  });

  // Modals: [data-modal-open="id"] opens, [data-modal-close] closes
  document.querySelectorAll('[data-modal-open]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const modal = document.getElementById(btn.dataset.modalOpen);
      if (modal) modal.classList.add('open');
    });
  });
  document.querySelectorAll('.modal-overlay').forEach((overlay) => {
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay || e.target.closest('[data-modal-close]')) {
        overlay.classList.remove('open');
      }
    });
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      document.querySelectorAll('.modal-overlay.open').forEach((m) => m.classList.remove('open'));
    }
  });
});

/* Chart.js defaults matching the JengaSec design system.
   Call after Chart.js loads, before creating charts. */
function jengasecChartDefaults() {
  if (typeof Chart === 'undefined') return;
  const styles = getComputedStyle(document.documentElement);
  Chart.defaults.font.family = "'Space Mono', monospace";
  Chart.defaults.font.size = 11;
  Chart.defaults.color = styles.getPropertyValue('--muted').trim() || '#5A6A5A';
  Chart.defaults.borderColor = styles.getPropertyValue('--border').trim() || '#CDD8CD';
}

/* Brand colour helpers for datasets */
const JENGASEC_COLORS = {
  green: '#007A3D',
  greenLight: '#00C853',
  gold: '#C9960A',
  goldLight: '#E8B020',
  danger: '#B3000C',
  dark: '#080C08',
};

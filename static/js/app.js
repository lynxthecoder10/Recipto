/**
 * Recipto - SaaS Interactions, Command Palette & Keyboard Navigation
 * Keyboard Shortcuts:
 *   Cmd/Ctrl + K  -> Command Palette
 *   Esc           -> Close drawer / Command Palette
 *   /             -> Focus Search input
 *   g then d      -> Dashboard
 *   g then b      -> Billing
 *   g then p      -> Payments
 *   g then h      -> History
 *   g then r      -> Reports
 *   g then s      -> Settings
 */

(function () {
  'use strict';

  /* ------------------------------------------------------------------------
     1. Lucide Icons Initializer
     ------------------------------------------------------------------------ */
  function renderIcons() {
    if (window.lucide && typeof window.lucide.createIcons === 'function') {
      window.lucide.createIcons();
    }
  }

  /* ------------------------------------------------------------------------
     2. Theme Engine (Light / Dark / System Mode)
     ------------------------------------------------------------------------ */
  const THEME_STORAGE_KEY = 'recipto-theme-pref';

  function getSystemTheme() {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  function applyTheme(theme) {
    const resolvedTheme = theme === 'system' ? getSystemTheme() : theme;
    document.documentElement.setAttribute('data-bs-theme', resolvedTheme);
    document.documentElement.setAttribute('data-theme-pref', theme);

    // Update active state in theme dropdown menu if present
    document.querySelectorAll('[data-set-theme]').forEach(btn => {
      if (btn.getAttribute('data-set-theme') === theme) {
        btn.classList.add('active');
      } else {
        btn.classList.remove('active');
      }
    });

    // Update Topbar Theme Icon
    const themeIconContainer = document.getElementById('currentThemeIcon');
    if (themeIconContainer) {
      if (theme === 'light') {
        themeIconContainer.setAttribute('data-lucide', 'sun');
      } else if (theme === 'dark') {
        themeIconContainer.setAttribute('data-lucide', 'moon');
      } else {
        themeIconContainer.setAttribute('data-lucide', 'monitor');
      }
      renderIcons();
    }
  }

  function setThemePreference(theme) {
    try {
      localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch (_) {}
    applyTheme(theme);
  }

  // Initialize theme
  const initialTheme = (function () {
    try {
      return localStorage.getItem(THEME_STORAGE_KEY) || 'system';
    } catch (_) {
      return 'system';
    }
  })();
  applyTheme(initialTheme);

  // Sync with OS dark/light mode
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    try {
      if ((localStorage.getItem(THEME_STORAGE_KEY) || 'system') === 'system') {
        applyTheme('system');
      }
    } catch (_) {}
  });

  /* ------------------------------------------------------------------------
     3. Floating Glass Navigation Drawer
     ------------------------------------------------------------------------ */
  const drawer = document.getElementById('appDrawer');
  const overlay = document.getElementById('sidebarOverlay');
  const menuToggleBtn = document.getElementById('menuToggleBtn');
  const drawerCloseBtn = document.getElementById('drawerCloseBtn');

  function openDrawer() {
    if (drawer && overlay) {
      drawer.classList.add('open');
      overlay.classList.add('active');
      document.body.style.overflow = 'hidden';
      if (menuToggleBtn) menuToggleBtn.setAttribute('aria-expanded', 'true');
    }
  }

  function closeDrawer() {
    if (drawer && overlay) {
      drawer.classList.remove('open');
      overlay.classList.remove('active');
      document.body.style.overflow = '';
      if (menuToggleBtn) menuToggleBtn.setAttribute('aria-expanded', 'false');
    }
  }

  function toggleDrawer() {
    if (drawer && drawer.classList.contains('open')) {
      closeDrawer();
    } else {
      openDrawer();
    }
  }

  if (menuToggleBtn) menuToggleBtn.addEventListener('click', toggleDrawer);
  if (drawerCloseBtn) drawerCloseBtn.addEventListener('click', closeDrawer);
  if (overlay) overlay.addEventListener('click', closeDrawer);

  // Auto-close drawer on navigation link click
  document.querySelectorAll('.drawer-nav-link').forEach(link => {
    link.addEventListener('click', () => {
      closeDrawer();
    });
  });

  /* ------------------------------------------------------------------------
     4. Real Command Palette Implementation
     ------------------------------------------------------------------------ */
  const paletteBackdrop = document.getElementById('commandPaletteBackdrop');
  const paletteInput = document.getElementById('commandSearchInput');
  const paletteItems = document.querySelectorAll('.command-item');
  let selectedIndex = 0;

  function openCommandPalette() {
    if (paletteBackdrop) {
      closeDrawer();
      paletteBackdrop.classList.add('active');
      document.body.style.overflow = 'hidden';
      if (paletteInput) {
        paletteInput.value = '';
        filterCommandItems('');
        setTimeout(() => paletteInput.focus(), 50);
      }
    }
  }

  function closeCommandPalette() {
    if (paletteBackdrop) {
      paletteBackdrop.classList.remove('active');
      document.body.style.overflow = '';
    }
  }

  function filterCommandItems(query) {
    const q = query.trim().toLowerCase();
    let visibleItems = [];
    paletteItems.forEach(item => {
      const text = (item.textContent || '').toLowerCase();
      if (!q || text.includes(q)) {
        item.style.display = 'flex';
        visibleItems.push(item);
      } else {
        item.style.display = 'none';
      }
    });

    selectedIndex = 0;
    updateSelectedCommandItem(visibleItems);
  }

  function updateSelectedCommandItem(visibleItems) {
    paletteItems.forEach(item => item.classList.remove('selected'));
    if (visibleItems && visibleItems.length > 0) {
      if (selectedIndex < 0) selectedIndex = 0;
      if (selectedIndex >= visibleItems.length) selectedIndex = visibleItems.length - 1;
      visibleItems[selectedIndex].classList.add('selected');
      visibleItems[selectedIndex].scrollIntoView({ block: 'nearest' });
    }
  }

  if (paletteInput) {
    paletteInput.addEventListener('input', (e) => {
      filterCommandItems(e.target.value);
    });

    paletteInput.addEventListener('keydown', (e) => {
      const visibleItems = Array.from(paletteItems).filter(item => item.style.display !== 'none');
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        selectedIndex = (selectedIndex + 1) % visibleItems.length;
        updateSelectedCommandItem(visibleItems);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        selectedIndex = (selectedIndex - 1 + visibleItems.length) % visibleItems.length;
        updateSelectedCommandItem(visibleItems);
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (visibleItems[selectedIndex]) {
          visibleItems[selectedIndex].click();
        }
      }
    });
  }

  if (paletteBackdrop) {
    paletteBackdrop.addEventListener('click', (e) => {
      if (e.target === paletteBackdrop) {
        closeCommandPalette();
      }
    });
  }

  /* ------------------------------------------------------------------------
     5. Real Keyboard Shortcuts (Cmd+K, Esc, /, g then d/b/p/h/r/s)
     ------------------------------------------------------------------------ */
  let lastGTime = 0;

  document.addEventListener('keydown', (e) => {
    const activeEl = document.activeElement;
    const isTyping = activeEl && (activeEl.tagName === 'INPUT' || activeEl.tagName === 'TEXTAREA' || activeEl.tagName === 'SELECT');

    // Cmd+K or Ctrl+K -> Command Palette
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      if (paletteBackdrop && paletteBackdrop.classList.contains('active')) {
        closeCommandPalette();
      } else {
        openCommandPalette();
      }
      return;
    }

    // Escape -> Close drawer or command palette
    if (e.key === 'Escape') {
      if (paletteBackdrop && paletteBackdrop.classList.contains('active')) {
        closeCommandPalette();
        return;
      }
      if (drawer && drawer.classList.contains('open')) {
        closeDrawer();
        return;
      }
    }

    // If typing in an input field, do not trigger single-key or sequence shortcuts
    if (isTyping) return;

    // '/' key -> Focus page search input
    if (e.key === '/') {
      const searchInput = document.querySelector('[data-table-filter]');
      if (searchInput) {
        e.preventDefault();
        searchInput.focus();
        searchInput.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
      return;
    }

    const now = Date.now();

    // Sequence 'g' then <key> (within 1000ms)
    if (e.key === 'g' || e.key === 'G') {
      lastGTime = now;
      return;
    }

    if (now - lastGTime < 1000) {
      const key = e.key.toLowerCase();
      lastGTime = 0; // reset
      if (key === 'd') {
        window.location.href = '/';
      } else if (key === 'b') {
        window.location.href = '/billing';
      } else if (key === 'p') {
        window.location.href = '/payments';
      } else if (key === 'h') {
        window.location.href = '/history';
      } else if (key === 'r') {
        window.location.href = '/reports';
      } else if (key === 's') {
        window.location.href = '/settings';
      }
    }
  });

  /* ------------------------------------------------------------------------
     6. Client-Side Instant Table Search Filter
     ------------------------------------------------------------------------ */
  document.querySelectorAll('[data-table-filter]').forEach(input => {
    const tableId = input.getAttribute('data-table-filter');
    const table = document.getElementById(tableId);
    if (!table) return;

    const tbody = table.querySelector('tbody');
    const emptyRow = document.getElementById(`${tableId}-empty`);

    input.addEventListener('input', () => {
      const query = input.value.trim().toLowerCase();
      const rows = tbody.querySelectorAll('tr');
      let visibleCount = 0;

      rows.forEach(row => {
        const text = row.textContent.toLowerCase();
        if (text.includes(query)) {
          row.style.display = '';
          visibleCount++;
        } else {
          row.style.display = 'none';
        }
      });

      if (emptyRow) {
        emptyRow.style.display = visibleCount === 0 ? 'block' : 'none';
      }
    });
  });

  /* ------------------------------------------------------------------------
     7. Theme Selection Click Handlers
     ------------------------------------------------------------------------ */
  document.addEventListener('click', (e) => {
    const themeBtn = e.target.closest('[data-set-theme]');
    if (themeBtn) {
      const selectedTheme = themeBtn.getAttribute('data-set-theme');
      setThemePreference(selectedTheme);
    }
  });

  /* ------------------------------------------------------------------------
     8. Auto-Dismissing Floating Toast Notifications
     ------------------------------------------------------------------------ */
  const toasts = document.querySelectorAll('.toast-custom');
  toasts.forEach(toast => {
    setTimeout(() => {
      toast.style.transition = 'all 0.28s cubic-bezier(0.16, 1, 0.3, 1)';
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(-10px) scale(0.96)';
      setTimeout(() => toast.remove(), 280);
    }, 5000);
  });

  // Global trigger function for search buttons
  window.openCommandPalette = openCommandPalette;
  window.closeCommandPalette = closeCommandPalette;

  // Initial Icon Render
  document.addEventListener('DOMContentLoaded', renderIcons);
  renderIcons();

})();

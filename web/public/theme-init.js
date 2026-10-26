(function () {
  var theme = 'dark';
  try {
    var saved = localStorage.getItem('immoalert.theme');
    if (saved === 'light' || saved === 'dark') theme = saved;
  } catch (_) {}
  document.documentElement.dataset.theme = theme;
})();

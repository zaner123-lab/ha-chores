// Show/hide frequency-detail and assignment-detail panels based on radio selection.
function refreshConditionalPanels(form) {
  // Frequency
  const freqRadio = form.querySelector('input[name="frequency_type"]:checked');
  const freq = freqRadio ? freqRadio.value : 'daily';
  form.querySelectorAll('.freq-detail').forEach(el => {
    el.classList.toggle('is-shown', el.dataset.when === freq);
  });

  // Assignment
  const assignRadio = form.querySelector('input[name="assignment_type"]:checked');
  const assign = assignRadio ? assignRadio.value : 'open';
  form.querySelectorAll('.assign-detail').forEach(el => {
    el.classList.toggle('is-shown', el.dataset.when === assign);
  });
}

document.addEventListener('change', (e) => {
  const form = e.target.closest('form.chore-form');
  if (!form) return;
  if (e.target.name === 'frequency_type' || e.target.name === 'assignment_type') {
    refreshConditionalPanels(form);
  }
});

// On load, initialize all chore forms (covers the inline-edit forms inside <details>).
document.querySelectorAll('form.chore-form').forEach(refreshConditionalPanels);

// When a <details class="claim"> is opened, close any other open ones.
document.addEventListener('click', (e) => {
  const claim = e.target.closest('details.claim');
  if (!claim) {
    document.querySelectorAll('details.claim[open]').forEach(d => d.removeAttribute('open'));
  }
});

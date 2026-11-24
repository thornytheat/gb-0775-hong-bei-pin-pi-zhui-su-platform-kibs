const layout = document.querySelector('#selection');
const scene = layout.querySelector('#scenario');
const originalScene = scene.value;
const work = layout.querySelector('#work-lot');
const archived = layout.querySelector('#scope-run');
scene.addEventListener('change', () => {
  const changed = scene.value !== originalScene;
  work.disabled = changed;
  if (archived) archived.disabled = changed;
});
document.querySelector('#print').addEventListener('click', () => print());

const reopen = (params) => {
  const query = new URLSearchParams({ sample:scene.value, work:work.value, ...params });
  if (archived) query.set('run', archived.value);
  location.assign(`/ledger/balance?${query}`);
};
const settle = document.querySelector('#close-month');
settle?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const message = settle.querySelector('output');
  const prices = {};
  settle.querySelectorAll('input[data-price]').forEach((input) => {
    if (input.value.trim()) prices[input.dataset.price] = input.value.trim();
  });
  const period = settle.querySelector('#close-period').value;
  message.textContent = '关账中…';
  try {
    const response = await fetch('/api/closing', { method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({ period, run:settle.dataset.run, prices }) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || '关账失败');
    reopen({ period });
  } catch (error) {
    message.textContent = `${error.message}，请重试。`;
  }
});
document.querySelectorAll('form.supplement').forEach((form) => {
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const message = form.querySelector('output');
    const { period, origin } = form.dataset;
    message.textContent = '补价入账中…';
    try {
      const response = await fetch(`/api/closing/${period}/supplements`, { method:'POST',
        headers:{'Content-Type':'application/json'}, body:JSON.stringify({ origin, price:form.querySelector('input[name=price]').value }) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.message || '补价失败');
      reopen({ period });
    } catch (error) {
      message.textContent = `${error.message}，请重试。`;
    }
  });
});

const commit = document.querySelector('#commit');
commit?.addEventListener('click', async () => {
  const message = document.querySelector('#message');
  const { key, sample, view } = commit.dataset;
  commit.disabled = true;
  message.textContent = '核对秤单中…';
  try {
    const response = await (key
      ? fetch(`/api/runs/${key}/rerun`, { method:'POST' })
      : fetch('/api/runs', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({sample}) }));
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || '卷联保存失败');
    if (key) message.textContent = '复算一致，称签仍用原卷。';
    else {
      const query = new URLSearchParams({ run:result.id, sample, work:work.value });
      location.assign(`/ledger/${view}?${query}`);
    }
  } catch (error) {
    message.textContent = `${error.message}，请重试。`;
  } finally { commit.disabled = false; }
});

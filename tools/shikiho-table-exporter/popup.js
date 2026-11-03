'use strict';

const csvButton = document.getElementById('csv');
const copyButton = document.getElementById('copy');
const statusElement = document.getElementById('status');

function status(message, error = false) {
  statusElement.textContent = message;
  statusElement.classList.toggle('error', error);
}
function busy(value) {
  csvButton.disabled = value;
  copyButton.disabled = value;
}

// Runs in the already-open screening results page; no background login, API or requests.
function extractVisibleResults() {
  if (location.hostname !== 'shikiho.toyokeizai.net' ||
      !location.pathname.startsWith('/screening/results')) {
    throw new Error('四季報オンラインの「スクリーニング結果」タブで実行してください。');
  }

  const normalize = value => (value || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
  const visible = element => {
    const style = window.getComputedStyle(element);
    return style.display !== 'none' && style.visibility !== 'hidden' && element.getClientRects().length > 0;
  };
  const grids = [...document.querySelectorAll('table')].filter(visible);
  // Result UI has a header-only table plus a second table containing its data rows.
  const candidateTables = grids.map(table => {
    const trs = [...table.querySelectorAll('tr')];
    const header = trs.find(tr => /コード/.test(normalize(tr.textContent)) && /銘柄名/.test(normalize(tr.textContent)));
    const rows = trs.filter(tr => {
      const cells = [...tr.querySelectorAll(':scope > td')];
      return cells.some(td => /^(?:\d{4}|\d{3}[A-Z])$/.test(normalize(td.textContent)));
    });
    return { table, header, rows };
  }).filter(entry => entry.header && entry.rows.length);

  if (!candidateTables.length) {
    throw new Error('銘柄データの表が見つかりません。結果の読み込み完了後に再実行してください。');
  }
  const selected = candidateTables.sort((a, b) => b.rows.length - a.rows.length)[0];
  const headers = [...selected.header.querySelectorAll(':scope > th, :scope > td')].map(cell => normalize(cell.textContent.replace(/Sort table by[\s\S]*/i, '')));
  const indices = headers.map((h, i) => ({ h, i })).filter(({h}) => h !== '');
  if (!indices.some(({h}) => h.includes('コード')) || !indices.some(({h}) => h.includes('銘柄名'))) {
    throw new Error('表の列見出しを判定できませんでした。');
  }
  const columns = indices.map(({h}) => h);
  const data = selected.rows.map(tr => {
    const cells = [...tr.querySelectorAll(':scope > td, :scope > th')];
    return indices.map(({i}) => normalize(cells[i]?.textContent ?? ''));
  });
  const codeIndex = columns.findIndex(c => c.includes('コード'));
  const unique = new Map();
  data.forEach(row => { if (row[codeIndex] && !unique.has(row[codeIndex])) unique.set(row[codeIndex], row); });
  if (unique.size === 0) throw new Error('有効な銘柄コードが取得できませんでした。');

  // An explicit safeguard: do not silently export only some results.
  const body = normalize(document.body.innerText);
  const match = body.match(/([\d,]+)件中\s*([\d,]+)件を表示/);
  const total = match ? Number(match[1].replace(/,/g, '')) : null;
  const shown = match ? Number(match[2].replace(/,/g, '')) : null;
  if (shown !== null && unique.size !== shown) {
    throw new Error(`画面では${shown}件表示ですが、${unique.size}件しか抽出できません。CSV保存・コピーを中止しました。`);
  }
  return { rows: [columns, ...unique.values()], count: unique.size, total };
}

function escapeCsv(value) {
  const text = String(value ?? '');
  return /[",\r\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
}
function makeCsv(rows) {
  return '\ufeff' + rows.map(row => row.map(escapeCsv).join(',')).join('\r\n') + '\r\n';
}
function makeTsv(rows) {
  // Tabs/newlines inside cells are normalized; spreadsheet paste remains rectangular.
  return rows.map(row => row.map(cell => String(cell ?? '').replace(/[\t\r\n]/g, ' ')).join('\t')).join('\r\n');
}
function timestamp() {
  const d = new Date();
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth()+1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}
async function readTable() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) throw new Error('現在のタブを取得できません。');
  if (!tab.url?.startsWith('https://shikiho.toyokeizai.net/screening/results')) {
    throw new Error('四季報オンラインのスクリーニング結果タブで実行してください。');
  }
  const results = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: extractVisibleResults });
  if (!results?.[0]?.result) throw new Error('表の読み取り結果がありません。');
  return results[0].result;
}
async function run(mode) {
  busy(true);
  status('表データを確認中…');
  try {
    const data = await readTable();
    if (mode === 'copy') {
      await navigator.clipboard.writeText(makeTsv(data.rows));
      status(`${data.count}銘柄をクリップボードにコピーしました。Excelなどに貼り付けてください。`);
    } else {
      const csv = makeCsv(data.rows);
      const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
      const objectUrl = URL.createObjectURL(blob);
      const fileName = `shikiho_screening_${timestamp()}.csv`;
      try {
        await chrome.downloads.download({ url: objectUrl, filename: fileName, saveAs: true });
        status(`${data.count}銘柄のCSV保存を開始しました。`);
      } finally {
        // Keep the blob URL alive briefly to allow the download to start.
        setTimeout(() => URL.revokeObjectURL(objectUrl), 30000);
      }
    }
  } catch (error) {
    status(`エラー：${error?.message || String(error)}`, true);
  } finally { busy(false); }
}
csvButton.addEventListener('click', () => run('csv'));
copyButton.addEventListener('click', () => run('copy'));
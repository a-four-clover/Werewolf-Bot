document.addEventListener('DOMContentLoaded', () => {
  const socket = io();
  const tableBody = document.querySelector('#roles-table tbody');
  const lastUpdated = document.getElementById('last-updated');

  function renderRoles(data){
    // data expected: dict or list
    let rows = [];
    if (!data) {
      tableBody.innerHTML = '<tr><td colspan="4">データが見つかりません</td></tr>';
      lastUpdated.textContent = 'データ: なし';
      return;
    }
    if (Array.isArray(data)){
      data.forEach(item => {
        if (typeof item === 'object'){
          const id = item.id || item.name || '';
          const name = item.name || id;
          const faction = item.faction || '';
          rows.push({id,name,faction});
        }
      });
    } else if (typeof data === 'object'){
      Object.keys(data).forEach(k => {
        const info = data[k];
        if (typeof info === 'object'){
          const name = info.name || k;
          const faction = info.faction || '';
          rows.push({id:k,name,faction});
        } else {
          rows.push({id:k,name: String(info), faction:''});
        }
      });
    }
    if (rows.length === 0){
      tableBody.innerHTML = '<tr><td colspan="4">データが空です</td></tr>';
      return;
    }
    // create table rows
    tableBody.innerHTML = '';
    rows.forEach(r => {
      const tr = document.createElement('tr');
      const tdId = document.createElement('td'); tdId.textContent = r.id; tdId.className = 'role-id';
      const tdName = document.createElement('td'); tdName.textContent = r.name; tdName.className = 'role-name';
      const tdJa = document.createElement('td'); tdJa.textContent = translateName(r.id); tdJa.className = 'role-name-ja';
      const tdFaction = document.createElement('td'); tdFaction.textContent = r.faction; tdFaction.className = 'role-faction';
      tr.appendChild(tdId); tr.appendChild(tdName); tr.appendChild(tdJa); tr.appendChild(tdFaction);
      tableBody.appendChild(tr);
    });
    lastUpdated.textContent = '更新: ' + new Date().toLocaleString();
  }

  // basic client-side translation mapping (should match server mapping)
  const JP = {
    'werewolf':'人狼','seer':'占い師','villager':'村人','madman':'狂人','medium':'霊媒師','nice_guesser':'善良な予言者','evil_guesser':'邪悪な予言者'
  };
  function translateName(id){
    return JP[id] || id;
  }

  socket.on('connect', () => {
    console.debug('connected to server');
  });
  socket.on('update', (payload) => {
    if (!payload) return;
    renderRoles(payload.data);
  });

});

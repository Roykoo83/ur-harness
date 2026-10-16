/* 운영 OS 데이터 원장 → §01 표 연동
 *
 * 표의 구조와 배지 체계는 그대로 두고, 손으로 채우던 세 칸만 원장에서 채운다.
 *
 * 표의 과제명과 원장을 **이름으로** 잇는다. 행 순서로 잇지 않는다 —
 * 표에서 한 줄만 빠져도 그 아래가 전부 밀려 엉뚱한 과제에 붙기 때문이다.
 * (실제로 9/14 판은 12행이고 원장은 13과제다.)
 *
 * 원장에 없는 것은 채우지 않는다. 빈 칸은 「관측 없음」으로 남긴다 —
 * 기계가 지어내면 그 순간 이 표를 믿을 수 없게 된다.
 */
(function () {
  if (typeof OPS_DATA === 'undefined') { console.warn('[원장] 최신.js 없음'); return; }

  var DONE  = /완료|끝냈|제출(?!\s*예정|\s*마감)|발송|수령|접수|회신|이체|납부|등록했|승인(?!\s*대기)|체결|확정|보완/;
  var BLOCK = /대기|지연|보류|막혔|불가|미정|검토\s*중|확인\s*필요|요청\s*중|이견|충돌/;
  var PLAN  = /예정|계획|하겠습니다|진행할|준비|바랍니다|요청/;

  function esc(s){ return String(s||'').replace(/[&<>"]/g, function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  function txt(el){ return (el.textContent || '').replace(/\s+/g,' ').trim(); }
  function norm(s){ return String(s||'').toLowerCase().replace(/[^0-9a-z가-힣]/g,''); }

  function d(s){ return new Date(String(s).slice(0,10) + 'T00:00:00+09:00'); }
  function ymd(x){ var p=function(n){ return String(n).padStart(2,'0'); };
    return x.getFullYear()+'.'+p(x.getMonth()+1)+'.'+p(x.getDate()); }

  // 기준 주: 이번 주 월요일. 지난주 실적 = 지난 월~일, 이번주 계획 = 이번 월~금
  var now = new Date(OPS_DATA.생성 || Date.now());
  var mon = new Date(now); mon.setHours(0,0,0,0);
  mon.setDate(mon.getDate() - ((mon.getDay()+6)%7));
  var lastMon = new Date(mon); lastMon.setDate(lastMon.getDate()-7);
  var lastSun = new Date(mon); lastSun.setDate(lastSun.getDate()-1);
  var thisFri = new Date(mon); thisFri.setDate(thisFri.getDate()+4);

  function bucket(it){
    var t = it.문장 || '', day = d(it.일자);
    if (BLOCK.test(t)) return '이슈';
    if (day >= mon) return '계획';                 // 이번 주 이후는 계획
    if (DONE.test(t)) return '실적';
    if (PLAN.test(t)) return '계획';
    return '실적';
  }
  function badge(it){
    var cls = it.신뢰도 === 'ledger' ? 'led' : 'obs';
    return '<span class="mini ' + cls + '">' + esc(it.출처 || '') + '</span>';
  }
  function li(it){
    return '<li><b>' + esc(String(it.일자||'').slice(5).replace('-','/')) + '</b> ' +
      esc(it.문장) + ' ' + badge(it) + '</li>';
  }
  function cell(items, empty){
    if (!items.length) return '<span class="mini unk">' + empty + '</span>';
    return '<ul class="li">' + items.slice(0,8).map(li).join('') +
      (items.length > 8 ? '<li style="opacity:.6">외 ' + (items.length-8) + '건</li>' : '') +
      '</ul>';
  }

  // ---- 표의 과제명 ↔ 원장 과제 잇기 (이름 기준)
  // 별칭 중 표 이름에 들어 있는 **가장 긴 것**이 이긴다.
  // 「팁스 창업사업화」와 「팁스 해외마케팅」처럼 짧은 별칭을 공유하는 과제가 있기 때문이다.
  function matchProject(name){
    var t = norm(name), best = null;
    (OPS_DATA.과제 || []).forEach(function (p) {
      (p.별칭 || []).concat([p.이름]).forEach(function (a) {
        var n = norm(a);
        if (n && n.length >= 2 && t.indexOf(n) >= 0 && (!best || n.length > best.len))
          best = { p: p, len: n.length };
      });
    });
    return best && best.p;
  }

  var sec = document.getElementById('s1');
  if (!sec) { console.warn('[원장] §01 없음'); return; }
  var rows = Array.prototype.filter.call(sec.querySelectorAll('tr'),
    function(tr){ return tr.querySelectorAll('td').length >= 6; });

  var filled = 0, total = 0, unmatched = [], seen = {};
  rows.forEach(function (tr) {
    var tds = tr.querySelectorAll('td');
    var name = txt(tds[2]);
    var p = matchProject(name);
    if (!p) { unmatched.push(name); return; }   // 못 이으면 **손으로 쓴 내용을 그대로 둔다**
    seen[p.key] = 1;

    var b = { 실적: [], 계획: [], 이슈: [] }, chat = 0;
    (p.항목 || []).forEach(function (it) {
      var day = d(it.일자);
      // 두 주 창(지난 월요일부터) 밖은 어느 칸에도 싣지 않는다.
      // 「이번주 계획」에 9월 1일 항목이 뜨면 그 칸을 아무도 안 믿는다.
      if (day < lastMon) return;
      // 방 대화 중 잡담·봇 명령은 세어서 한 줄로 접는다. 무엇이 잡담인지는
      // 원장이 정해 `업무: false` 로 표시해 둔다 — 화면과 주간보고가 같은 판정을 쓴다.
      // 원장(최신.md)에는 그대로 남아 있으므로 지운 것이 아니다.
      if (it.업무 === false) { chat++; return; }   // 판정은 원장이 한다
      var k = bucket(it);
      if (k === '실적' && day > lastSun) k = '계획';   // 이번 주 완료분은 계획 칸에
      b[k].push(it);
    });
    ['실적','계획','이슈'].forEach(function (k) {
      b[k].sort(function(x,y){ return String(y.일자).localeCompare(String(x.일자)); });
      total += b[k].length;
    });
    tds[3].innerHTML = cell(b.실적, '관측 없음') + (chat
      ? '<div class="mini" style="margin-top:6px">방 대화 ' + chat + '건 (잡담·봇 명령 · 원장에는 있음)</div>'
      : '');
    tds[4].innerHTML = cell(b.계획, '관측 없음');
    tds[5].innerHTML = cell(b.이슈, '없음');
    filled++;
  });

  // 원장에는 있는데 표에 행이 없는 과제 — 감추지 않고 알린다
  var missing = (OPS_DATA.과제 || []).filter(function(p){
    return !seen[p.key] && (p.항목 || []).length; }).map(function(p){ return p.이름; });

  // ---- 표 머리의 기간 갱신 (손으로 적은 날짜는 반드시 낡는다)
  var ths = sec.querySelectorAll('th');
  if (ths.length >= 6) {
    ths[3].innerHTML = '지난주 실적<span style="font-weight:400;opacity:.7">' +
      ymd(lastMon) + ' ~ ' + ymd(lastSun) + '</span>';
    ths[4].innerHTML = '이번주 계획<span style="font-weight:400;opacity:.7">' +
      ymd(mon) + ' ~ ' + ymd(thisFri) + '</span>';
  }

  // ---- 데이터 기준 카드 채우기
  //
  // 카드 글씨를 내가 직접 쓰지 않는다. **data-at / data-cycle 만 채우고 화면의
  // renderStreams() 를 부른다.** 그래야 상단 「스트림 N개 최신」 배지와
  // 「가장 낡은 스트림」까지 같은 값으로 함께 맞는다.
  var MAP = {
    '메일':'메일', '캘린더':'캘린더', 'SSOT 산출물':'SSOT 산출물', '부서방':'부서방',
    'AX_TF':'AX_TF', '주간보고':'주간보고',
    '회의록':'회의록', '회의록 원장':'회의록', '회의록 액션':'회의록',
    '예산 원장':'예산 원장', 'U드라이브':'U 드라이브', 'U 드라이브':'U 드라이브'
  };
  var src = OPS_DATA.원천 || {};
  var grid = document.querySelector('.st-grid');
  if (grid) {
    // 봇이 들어가 있는 방은 둘이다. AX_TF 카드가 없으면 부서방 뒤에 만든다
    if (!Array.prototype.some.call(grid.children, function(c){
          var b=c.querySelector('b'); return b && txt(b)==='AX_TF'; })) {
      var dept = Array.prototype.filter.call(grid.children, function(c){
        var b=c.querySelector('b'); return b && txt(b)==='부서방'; })[0];
      if (dept) {
        var n = document.createElement('div');
        n.className = 'st';
        n.innerHTML = '<b>AX_TF</b><span></span><i></i>';
        dept.parentNode.insertBefore(n, dept.nextSibling);
      }
    }
    Array.prototype.forEach.call(grid.querySelectorAll('.st'), function (card) {
      var b = card.querySelector('b'); if (!b) return;
      var v = src[MAP[txt(b)]];
      if (!v) return;
      var note = card.querySelector('span');
      if (note) note.textContent = v.설명 || '';
      if (!card.querySelector('i')) card.appendChild(document.createElement('i'));
      card.classList.remove('warn','bad','nolink');
      card.setAttribute('data-cycle', v.주기 || '');
      if (!v.t) {                    // 고장이 아니라 아직 안 만든 것. 낡은 날짜를 띄우지 않는다
        card.classList.add('nolink');
        card.removeAttribute('data-at');
        card.querySelector('i').textContent = '스트림 없음 · ' + (v.주기 || '');
        return;
      }
      card.setAttribute('data-at', v.t.replace(' ', 'T'));
    });

    // 화면이 자기 방식으로 다시 그리게 한다. 없으면 내가 쓴다
    if (typeof renderStreams === 'function') { try { renderStreams(); } catch (e) {} }
    else {
      Array.prototype.forEach.call(grid.querySelectorAll('.st[data-at]'), function (card) {
        var at = new Date(card.getAttribute('data-at') + '+09:00');
        var days = Math.floor((now - at) / 86400000);
        if (days >= 7) card.classList.add('bad'); else if (days >= 2) card.classList.add('warn');
        card.querySelector('i').textContent =
          card.getAttribute('data-at').slice(5).replace('T', ' ') + ' · ' +
          (days <= 0 ? '오늘' : days + '일 전') + ' · ' + card.getAttribute('data-cycle');
      });
    }

    // 절 제목 옆 한 줄. 손으로 적힌 문구를 실측으로 덮는다
    var sub = document.querySelector('.st-head .st-s');
    if (sub) {
      var notes = ['원장 ' + OPS_DATA.회차 + ' 기준 · 자동 갱신'];
      var stale = Object.keys(src).filter(function (k) {
        var t = src[k].t; if (!t) return false;
        return Math.floor((now - new Date(t.replace(' ', 'T') + '+09:00')) / 86400000) >= 7;
      });
      if (stale.length) notes.push('7일 이상 낡음: ' + stale.join(', '));
      if (OPS_DATA.결손 && OPS_DATA.결손.length)
        notes.push('결손 회차 ' + OPS_DATA.결손.length + '회 — 그만큼 공백이 있습니다');
      if (unmatched.length) notes.push('원장과 못 이은 행: ' + unmatched.join(', '));
      if (missing.length) notes.push('표에 행이 없는 과제: ' + missing.join(', '));
      sub.textContent = notes.join(' · ');
    }
  }

  console.log('[원장] ' + OPS_DATA.회차 + ' · ' + filled + '행 · ' + total + '항목' +
    (unmatched.length ? ' · 못 이은 행: ' + unmatched.join(', ') : '') +
    (missing.length ? ' · 표에 행이 없는 과제: ' + missing.join(', ') : ''));
})();

// ============================================================
//  6 Nimmt! - WeChat Mini Program
//  Ported from index.html - same logic, pure ASCII only
// ============================================================

var TOTAL = 100;
var NUM_ROWS = 5;
var MAX_ROW = 6;
var HAND_SIZE = 10;
var END_SCORE = 66;
var NUM_PLAYERS = 6;

var PLAYER_NAMES = [
  "你",
  "🐂 周胖子养皀480头牛",
  "🫏 驴先生努力养牛",
  "🍷 李女士请继续喝",
  "🐟 rb你养鱼呢",
  "🐥 一群小菜鸡"
];
var AI_STRATEGIES = ["greedy", "safe", "greedy", "random", "safe"];

function getBulls(card) {
  if (card === 55) return 7;
  if (card % 11 === 0) return 5;
  if (card % 10 === 0) return 3;
  if (card % 5 === 0) return 2;
  return 1;
}

function bullsStr(n) {
  var s = "";
  var i;
  for (i = 0; i < n; i++) {
    s += "🐂";
  }
  return s;
}

function shuffle(arr) {
  var i, j, tmp;
  for (i = arr.length - 1; i > 0; i--) {
    j = Math.floor(Math.random() * (i + 1));
    tmp = arr[i];
    arr[i] = arr[j];
    arr[j] = tmp;
  }
  return arr;
}

function makeRange(n) {
  var r = [];
  var i;
  for (i = 0; i < n; i++) { r.push(i); }
  return r;
}

function deal() {
  var deck = shuffle(makeRange(TOTAL).map(function(v) { return v + 1; }));
  var hands = [];
  var i;
  for (i = 0; i < NUM_PLAYERS; i++) {
    hands.push(deck.slice(i * HAND_SIZE, (i + 1) * HAND_SIZE).sort(function(a, b) { return a - b; }));
  }
  var rows = [];
  var start = NUM_PLAYERS * HAND_SIZE;
  // base cards sorted ascending - small in row 1, large in row 5
  var baseCards = deck.slice(start, start + NUM_ROWS).sort(function(a, b) { return a - b; });
  var r;
  for (r = 0; r < NUM_ROWS; r++) {
    rows.push([baseCards[r]]);
  }
  return { hands: hands, rows: rows };
}

function rowBulls(row) {
  return row.reduce(function(s, c) { return s + getBulls(c); }, 0);
}

function findBestRow(rows, card) {
  var best = -1;
  var minDiff = Infinity;
  var r;
  for (r = 0; r < NUM_ROWS; r++) {
    var tail = rows[r][rows[r].length - 1];
    if (tail < card && (card - tail) < minDiff) {
      minDiff = card - tail;
      best = r;
    }
  }
  return best;
}

function aiChooseCard(hand, rows, strategy) {
  var scored = hand.map(function(card) {
    var br = findBestRow(rows, card);
    var risk;
    if (br === -1) {
      risk = Math.min.apply(null, rows.map(rowBulls)) + 80;
    } else if (rows[br].length >= MAX_ROW) {
      risk = rowBulls(rows[br]) + 40;
    } else {
      risk = rows[br].length * 3;
    }
    return { card: card, risk: risk };
  });

  scored.sort(function(a, b) { return a.risk - b.risk; });

  if (strategy === "random") {
    return hand[Math.floor(Math.random() * hand.length)];
  }

  if (strategy === "safe") {
    if (Math.random() < 0.1) {
      var pool = scored.slice(0, Math.ceil(scored.length / 2));
      return pool[Math.floor(Math.random() * pool.length)].card;
    }
    return scored[0].card;
  }

  if (strategy === "greedy") {
    var rnd = Math.random();
    if (rnd < 0.70) {
      var gpool = scored.slice(0, Math.max(1, Math.floor(scored.length / 3)));
      return gpool[Math.floor(Math.random() * gpool.length)].card;
    } else if (rnd < 0.90) {
      var mid = Math.floor(scored.length / 2);
      var mpool = scored.slice(mid - 1, mid + 2).filter(Boolean);
      return mpool[Math.floor(Math.random() * mpool.length)].card;
    } else {
      return hand[hand.length - 1];
    }
  }

  // default: noisy greedy
  var noisy = scored.map(function(x) {
    return { card: x.card, score: x.risk + (Math.random() * 10 - 3) };
  });
  noisy.sort(function(a, b) { return a.score - b.score; });
  return noisy[0].card;
}

function aiChooseRow(rows) {
  var best = 0;
  var minB = Infinity;
  var r;
  for (r = 0; r < NUM_ROWS; r++) {
    var b = rowBulls(rows[r]);
    if (b < minB) { minB = b; best = r; }
  }
  return best;
}

// Helper: build card data object for WXML rendering
// Pre-computes everything so WXML never needs to call functions
function buildCardData(card, extraCls, onclickKey) {
  var b = getBulls(card);
  return {
    num: card,
    bulls: b,
    bullsEmoji: bullsStr(b),
    cls: "b" + b + (extraCls ? " " + extraCls : ""),
    onclickKey: onclickKey || ""
  };
}

// Helper: build row data with pre-computed card info and slot info
function buildRowData(rows) {
  return rows.map(function(row, ri) {
    var cards = row.map(function(c) {
      return buildCardData(c, "", "");
    });
    var slots = [];
    var si;
    for (si = 0; si < MAX_ROW; si++) {
      slots.push({ filled: si < row.length });
    }
    return {
      index: ri,
      label: "" + (ri + 1),
      cards: cards,
      slots: slots,
      isFull: row.length >= MAX_ROW,
      totalBulls: rowBulls(row)
    };
  });
}

// Helper: build hand data with pre-computed card info
function buildHandData(hand, phase, selectedCard) {
  return hand.map(function(card) {
    var isSelected = (card === selectedCard);
    var isPickPhase = (phase === "pick");
    var extraCls = "";
    if (isPickPhase) {
      extraCls = isSelected ? "selectable selected" : "selectable";
    }
    return buildCardData(card, extraCls, isPickPhase ? "1" : "");
  });
}

Page({
  data: {
    phase: 'start',
    playerNames: PLAYER_NAMES,
    scores: [],
    rowData: [],
    handData: [],
    round: 1,
    selectedCard: null,
    actionMsg: '',
    logs: [],
    logScrollTop: 99999,
    showRowModal: false,
    showEndModal: false,
    endTitle: '',
    endDesc: '',
    endRanking: [],
    choosingForCard: null,
    _chosen: [],
    _pendingQueue: [],
    _pendingIdx: 0
  },

  startGame: function() {
    var result = deal();
    var hands = result.hands;
    var rows = result.rows;

    this.setData({
      phase: 'pick',
      _hands: hands,
      _rows: rows,
      scores: makeRange(NUM_PLAYERS).map(function() { return 0; }),
      rowData: buildRowData(rows),
      handData: buildHandData(hands[0], 'pick', null),
      round: 1,
      selectedCard: null,
      actionMsg: '👆 tap your card to play',
      logs: [{ msg: 'Game Start! 🎲', cls: 'system' }],
      showRowModal: false,
      showEndModal: false,
      _chosen: makeRange(NUM_PLAYERS).map(function() { return null; }),
      _pendingQueue: [],
      _pendingIdx: 0,
      choosingForCard: null
    });
  },

  onCardClick: function(e) {
    if (this.data.phase !== 'pick') return;
    var card = e.currentTarget.dataset.card;
    var b = getBulls(card);

    this.setData({
      selectedCard: card,
      handData: buildHandData(this.data._hands[0], 'pick', card),
      actionMsg: 'Selected: ' + card + ' (' + bullsStr(b) + ') - tap other to change'
    });
  },

  confirmPlay: function() {
    var sc = this.data.selectedCard;
    if (!sc) return;

    var chosen = this.data._chosen.slice();
    chosen[0] = sc;

    var i;
    for (i = 1; i < NUM_PLAYERS; i++) {
      chosen[i] = aiChooseCard(this.data._hands[i], this.data._rows, AI_STRATEGIES[i - 1]);
    }

    // Remove played cards from hands
    var newHands = [];
    for (i = 0; i < NUM_PLAYERS; i++) {
      var h = this.data._hands[i].slice();
      var idx = h.indexOf(chosen[i]);
      if (idx !== -1) h.splice(idx, 1);
      newHands.push(h);
    }

    var newLogs = this.data.logs.slice();
    newLogs.unshift({
      msg: 'Round ' + this.data.round + ': you played ' + chosen[0] + ' (' + bullsStr(getBulls(chosen[0])) + ')',
      cls: 'you'
    });

    var pendingQueue = makeRange(NUM_PLAYERS)
      .sort(function(a, b) { return chosen[a] - chosen[b]; });

    this.setData({
      _hands: newHands,
      _chosen: chosen,
      _pendingQueue: pendingQueue,
      _pendingIdx: 0,
      phase: 'resolve',
      logs: newLogs.slice(0, 60),
      logScrollTop: 99999,
      handData: []
    }, function() {
      setTimeout(function() { this.resolveNext(); }.bind(this), 200);
    }.bind(this));
  },

  resolveNext: function() {
    var pq = this.data._pendingQueue;
    var pi = this.data._pendingIdx;

    if (pi >= pq.length) {
      // Round over
      var nr = this.data.round + 1;
      var hands0 = this.data._hands ? this.data._hands[0] : [];

      if (hands0.length === 0 || Math.max.apply(null, this.data.scores) >= END_SCORE) {
        this.setData({ phase: 'end', selectedCard: null, handData: [] });
        this.showEndModalFn();
        return;
      }

      this.setData({
        phase: 'pick',
        round: nr,
        selectedCard: null,
        actionMsg: '👆 tap your card to play',
        handData: buildHandData(hands0, 'pick', null)
      });
      return;
    }

    var playerIdx = pq[pi];
    var card = this.data._chosen[playerIdx];
    var rows = this.data._rows;
    var br = findBestRow(rows, card);

    if (br === -1) {
      if (playerIdx === 0) {
        this.setData({
          phase: 'choose_row',
          choosingForCard: card,
          showRowModal: true,
          actionMsg: '😱 your card ' + card + ' is smallest! pick a row'
        });
      } else {
        this.doAiPickRow(playerIdx, card);
      }
    } else if (rows[br].length >= MAX_ROW) {
      this.doCollectRow(playerIdx, card, br);
    } else {
      this.doPlaceCard(playerIdx, card, br);
    }
  },

  doAiPickRow: function(pi, card) {
    var r = aiChooseRow(this.data._rows);
    var penalty = rowBulls(this.data._rows[r]);

    var newScores = this.data.scores.slice();
    newScores[pi] += penalty;

    var newRows = [];
    var ri;
    for (ri = 0; ri < NUM_ROWS; ri++) {
      newRows.push(this.data._rows[ri].slice());
    }
    newRows[r] = [card];

    var newLogs = this.data.logs.slice();
    newLogs.unshift({
      msg: PLAYER_NAMES[pi] + ' played ' + card + ', took row ' + (r+1) + ' -' + penalty + '🐂',
      cls: penalty > 0 ? 'penalty' : ''
    });

    this.setData({
      _scores: newScores,
      scores: newScores,
      _rows: newRows,
      rowData: buildRowData(newRows),
      logs: newLogs.slice(0, 60),
      _pendingIdx: this.data._pendingIdx + 1,
      logScrollTop: 99999
    }, function() {
      setTimeout(function() { this.resolveNext(); }.bind(this), 300);
    }.bind(this));
  },

  doCollectRow: function(pi, card, br) {
    var penalty = rowBulls(this.data._rows[br]);

    var newScores = this.data.scores.slice();
    newScores[pi] += penalty;

    var newRows = [];
    var ri;
    for (ri = 0; ri < NUM_ROWS; ri++) {
      newRows.push(this.data._rows[ri].slice());
    }
    newRows[br] = [card];

    var marker = pi === 0 ? ' (you)' : '';
    var newLogs = this.data.logs.slice();
    newLogs.unshift({
      msg: PLAYER_NAMES[pi] + marker + ' played ' + card + ', row ' + (br+1) + ' full -' + penalty + '🐂',
      cls: penalty > 0 ? 'penalty' : ''
    });

    this.setData({
      _scores: newScores,
      scores: newScores,
      _rows: newRows,
      rowData: buildRowData(newRows),
      logs: newLogs.slice(0, 60),
      _pendingIdx: this.data._pendingIdx + 1,
      logScrollTop: 99999
    }, function() {
      setTimeout(function() { this.resolveNext(); }.bind(this), 300);
    }.bind(this));
  },

  doPlaceCard: function(pi, card, br) {
    var newRows = [];
    var ri;
    for (ri = 0; ri < NUM_ROWS; ri++) {
      newRows.push(this.data._rows[ri].slice());
    }
    newRows[br].push(card);

    var marker = pi === 0 ? ' (you)' : '';
    var newLogs = this.data.logs.slice();
    newLogs.unshift({
      msg: PLAYER_NAMES[pi] + marker + ' played ' + card + ' -> row ' + (br+1),
      cls: ''
    });

    this.setData({
      _rows: newRows,
      rowData: buildRowData(newRows),
      logs: newLogs.slice(0, 60),
      _pendingIdx: this.data._pendingIdx + 1,
      logScrollTop: 99999
    }, function() {
      setTimeout(function() { this.resolveNext(); }.bind(this), 200);
    }.bind(this));
  },

  humanChooseRow: function(e) {
    var r = parseInt(e.currentTarget.dataset.row);
    var card = this.data.choosingForCard;
    var penalty = rowBulls(this.data._rows[r]);

    var newScores = this.data.scores.slice();
    newScores[0] += penalty;

    var newRows = [];
    var ri;
    for (ri = 0; ri < NUM_ROWS; ri++) {
      newRows.push(this.data._rows[ri].slice());
    }
    newRows[r] = [card];

    var newLogs = this.data.logs.slice();
    newLogs.unshift({
      msg: 'You took row ' + (r+1) + ' -' + penalty + '🐂',
      cls: 'penalty'
    });

    this.setData({
      _scores: newScores,
      scores: newScores,
      _rows: newRows,
      rowData: buildRowData(newRows),
      showRowModal: false,
      phase: 'resolve',
      _pendingIdx: this.data._pendingIdx + 1,
      logs: newLogs.slice(0, 60),
      logScrollTop: 99999
    }, function() {
      setTimeout(function() { this.resolveNext(); }.bind(this), 300);
    }.bind(this));
  },

  showEndModalFn: function() {
    var ranking = makeRange(NUM_PLAYERS)
      .sort(function(a, b) { return this.data.scores[a] - this.data.scores[b]; }.bind(this));

    var winner = ranking[0];
    var humanRank = ranking.indexOf(0) + 1;
    var medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣"];

    var self = this;
    this.setData({
      showEndModal: true,
      endTitle: winner === 0 ? "🎉 You Win!" : PLAYER_NAMES[winner] + " Wins!",
      endDesc: winner === 0 ? "Least bulls!" : "Your rank: " + humanRank,
      endRanking: ranking.map(function(pi, rank) {
        return {
          pi: pi,
          medal: medals[rank],
          name: PLAYER_NAMES[pi],
          isYou: pi === 0,
          score: self.data.scores[pi]
        };
      }),
      actionMsg: '🏁 Game Over!'
    });
  }
});


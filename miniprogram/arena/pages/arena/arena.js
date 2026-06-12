// ============================================================
//  Arena Page - Battle against trained RL models
//  Calls server API for AI decisions
// ============================================================

var TOTAL = 100;
var NUM_ROWS = 5;
var MAX_ROW = 6;
var HAND_SIZE = 10;
var END_SCORE = 66;
var NUM_PLAYERS = 6;

var ARENA_NAMES = ["你", "🤖 PPO", "🔵 DQN", "🟢 QL", "🔴 AI甲", "🟡 AI乙"];
var AI_STRATEGIES = ["greedy", "safe", "greedy", "random", "safe"];

// Server URL - change this to your server's IP/domain
var SERVER_URL = "http://localhost:8000";

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
  for (i = 0; i < n; i++) { s += "🐂"; }
  return s;
}

function shuffle(arr) {
  var i, j, tmp;
  for (i = arr.length - 1; i > 0; i--) {
    j = Math.floor(Math.random() * (i + 1));
    tmp = arr[i]; arr[i] = arr[j]; arr[j] = tmp;
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
  var baseCards = deck.slice(start, start + NUM_ROWS).sort(function(a, b) { return a - b; });
  var r;
  for (r = 0; r < NUM_ROWS; r++) { rows.push([baseCards[r]]); }
  return { hands: hands, rows: rows };
}

function rowBulls(row) {
  return row.reduce(function(s, c) { return s + getBulls(c); }, 0);
}

function findBestRow(rows, card) {
  var best = -1, minDiff = Infinity;
  var ri;
  for (ri = 0; ri < NUM_ROWS; ri++) {
    var tail = rows[ri][rows[ri].length - 1];
    if (tail < card && (card - tail) < minDiff) { minDiff = card - tail; best = ri; }
  }
  return best;
}

function aiChooseCard(hand, rows, strategy) {
  var scored = hand.map(function(card) {
    var br = findBestRow(rows, card);
    var risk;
    if (br === -1) { risk = Math.min.apply(null, rows.map(rowBulls)) + 80; }
    else if (rows[br].length >= MAX_ROW) { risk = rowBulls(rows[br]) + 40; }
    else { risk = rows[br].length * 3; }
    return { card: card, risk: risk };
  });
  scored.sort(function(a, b) { return a.risk - b.risk; });

  if (strategy === "random") { return hand[Math.floor(Math.random() * hand.length)]; }
  if (strategy === "safe") { return scored[0].card; }

  var rnd = Math.random();
  if (rnd < 0.70) {
    var pool = scored.slice(0, Math.max(1, Math.floor(scored.length / 3)));
    return pool[Math.floor(Math.random() * pool.length)].card;
  } else if (rnd < 0.90) {
    var mid = Math.floor(scored.length / 2);
    var mpool = scored.slice(mid - 1, mid + 2).filter(Boolean);
    return mpool[Math.floor(Math.random() * mpool.length)].card;
  } else { return hand[hand.length - 1]; }
}

function aiChooseRow(rows) {
  var best = 0, minB = Infinity;
  var r;
  for (r = 0; r < NUM_ROWS; r++) {
    var b = rowBulls(rows[r]);
    if (b < minB) { minB = b; best = r; }
  }
  return best;
}

// Call server RL model API
function callServerAI(modelType, hand, rows, score, callback) {
  wx.request({
    url: SERVER_URL + '/api/arena/action',
    method: 'POST',
    data: {
      model_type: modelType,
      hand: hand,
      rows: rows,
      score: score
    },
    success: function(res) {
      if (res.statusCode === 200 && res.data) {
        callback(null, res.data.card_index, res.data.card, res.data.is_fallback);
      } else {
        // Fallback to local rule-based
        var card = aiChooseCard(hand, modelType === 'ql' ? 'safe' : 'greedy');
        callback(null, hand.indexOf(card), card, true);
      }
    },
    fail: function(err) {
      // Server unreachable - fallback
      console.log('Server unreachable, using local AI');
      var card = aiChooseCard(hand, modelType === 'ql' ? 'safe' : 'greedy');
      callback(err, hand.indexOf(card), card, true);
    }
  });
}

// Build card data for WXML
function buildCardData(card, extraCls) {
  var b = getBulls(card);
  return {
    num: card,
    bulls: b,
    bullsEmoji: bullsStr(b),
    cls: "b" + b + (extraCls ? " " + extraCls : "")
  };
}

function buildRowData(rows) {
  return rows.map(function(row, ri) {
    var cards = row.map(function(c) { return buildCardData(c, ""); });
    var slots = [];
    var si;
    for (si = 0; si < MAX_ROW; si++) { slots.push({ filled: si < row.length }); }
    return {
      index: ri, label: "" + (ri + 1), cards: cards,
      slots: slots, isFull: row.length >= MAX_ROW, totalBulls: rowBulls(row)
    };
  });
}

function buildHandData(hand, phase, selectedCard) {
  return hand.map(function(card) {
    var isSelected = (card === selectedCard);
    var isPickPhase = (phase === "pick");
    var extraCls = "";
    if (isPickPhase) { extraCls = isSelected ? "selectable selected" : "selectable"; }
    return buildCardData(card, extraCls);
  });
}

Page({
  data: {
    phase: 'start',
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
    modelStatus: {},
    _hands: [],
    _rows: [],
    _chosen: [],
    _pendingQueue: [],
    _pendingIdx: 0
  },

  onLoad: function() {
    this.checkModels();
  },

  checkModels: function() {
    var self = this;
    wx.request({
      url: SERVER_URL + '/api/arena/models',
      success: function(res) {
        if (res.statusCode === 200) {
          self.setData({ modelStatus: res.data.status || {} });
        }
      },
      fail: function() {
        self.setData({
          modelStatus: { ppo: false, dqn: false, ql: false, error: 'Server offline' }
        });
      }
    });
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
      logs: [{ msg: 'Arena Start! 🎮 PPO vs DQN vs QL vs You', cls: 'system' }],
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
    this.setData({
      selectedCard: card,
      handData: buildHandData(this.data._hands[0], 'pick', card),
      actionMsg: 'Selected: ' + card + ' - tap other to change'
    });
  },

  confirmPlay: function() {
    var sc = this.data.selectedCard;
    if (!sc) return;

    var chosen = this.data._chosen.slice();
    chosen[0] = sc;

    var newLogs = this.data.logs.slice();
    newLogs.unshift({
      msg: 'R' + this.data.round + ': you played ' + sc + ' (' + bullsStr(getBulls(sc)) + ')',
      cls: 'you'
    });

    // Remove player's played card from hand
    var newHands = [];
    var i;
    for (i = 0; i < NUM_PLAYERS; i++) {
      var h = this.data._hands[i].slice();
      if (i === 0) {
        var idx = h.indexOf(sc);
        if (idx !== -1) h.splice(idx, 1);
      }
      newHands.push(h);
    }

    this.setData({
      _hands: newHands,
      _chosen: chosen,
      phase: 'resolve',
      logs: newLogs.slice(0, 60),
      logScrollTop: 99999,
      handData: []
    }, function() {
      // Now resolve AI moves one by one (calling server)
      this.resolveNextAI(1);  // start with player 1 (PPO)
    }.bind(this));
  },

  // Resolve each AI player by calling the server
  resolveNextAI: function(playerIdx) {
    if (playerIdx >= NUM_PLAYERS) {
      // All AI done, now sort and place cards
      this.resolveAllCards();
      return;
    }

    var self = this;
    var hand = this.data._hands[playerIdx];
    var rows = this.data._rows;
    var score = this.data.scores[playerIdx];

    if (!hand || hand.length === 0) {
      this.resolveNextAI(playerIdx + 1);
      return;
    }

    // Map player index to model type
    var modelTypes = [null, "ppo", "dqn", "ql", null, null];  // 4,5 are rule-based
    var modelType = modelTypes[playerIdx];

    if (!modelType) {
      // Rule-based AI (players 4,5)
      var card = aiChooseCard(hand, AI_STRATEGIES[playerIdx - 1]);
      var chosen = this.data._chosen.slice();
      chosen[playerIdx] = card;
      var h = this.data._hands[playerIdx].slice();
      var ci = h.indexOf(card);
      if (ci !== -1) h.splice(ci, 1);
      var newHands = this.data._hands.slice();
      newHands[playerIdx] = h;

      this.setData({ _chosen: chosen, _hands: newHands }, function() {
        self.resolveNextAI(playerIdx + 1);
      });
      return;
    }

    // Call server for RL model
    this.setData({ actionMsg: ARENA_NAMES[playerIdx] + ' is thinking... 🤔' });

    callServerAI(modelType, hand, rows, score, function(err, idx, card, isFallback) {
      var chosen = self.data._chosen.slice();
      chosen[playerIdx] = card;
      var h = self.data._hands[playerIdx].slice();
      var ci = h.indexOf(card);
      if (ci !== -1) h.splice(ci, 1);
      var newHands = self.data._hands.slice();
      newHands[playerIdx] = h;

      var tag = isFallback ? '(fallback)' : '';
      var newLogs = self.data.logs.slice();
      newLogs.unshift({
        msg: ARENA_NAMES[playerIdx] + tag + ' played ' + card,
        cls: ''
      });

      self.setData({
        _chosen: chosen,
        _hands: newHands,
        logs: newLogs.slice(0, 60),
        logScrollTop: 99999
      }, function() {
        self.resolveNextAI(playerIdx + 1);
      });
    });
  },

  resolveAllCards: function() {
    var chosen = this.data._chosen;
    var pendingQueue = makeRange(NUM_PLAYERS)
      .sort(function(a, b) { return chosen[a] - chosen[b]; });

    this.setData({
      _pendingQueue: pendingQueue,
      _pendingIdx: 0
    }, function() {
      this.resolveNext();
    }.bind(this));
  },

  resolveNext: function() {
    var pq = this.data._pendingQueue;
    var pi = this.data._pendingIdx;

    if (pi >= pq.length) {
      var nr = this.data.round + 1;
      var hands0 = this.data._hands[0];

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
        handData: buildHandData(hands0, 'pick', null),
        _chosen: makeRange(NUM_PLAYERS).map(function() { return null; })
      });
      return;
    }

    var playerIdx = pq[pi];
    var card = chosen[playerIdx];  // BUG FIX: should be this.data._chosen
    card = this.data._chosen[playerIdx];
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
    for (ri = 0; ri < NUM_ROWS; ri++) { newRows.push(this.data._rows[ri].slice()); }
    newRows[r] = [card];

    var newLogs = this.data.logs.slice();
    newLogs.unshift({
      msg: ARENA_NAMES[pi] + ' took row ' + (r+1) + ' -' + penalty + '🐂',
      cls: penalty > 0 ? 'penalty' : ''
    });

    this.setData({
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
    for (ri = 0; ri < NUM_ROWS; ri++) { newRows.push(this.data._rows[ri].slice()); }
    newRows[br] = [card];

    var marker = pi === 0 ? ' (you)' : '';
    var newLogs = this.data.logs.slice();
    newLogs.unshift({
      msg: ARENA_NAMES[pi] + marker + ' row ' + (br+1) + ' full -' + penalty + '🐂',
      cls: penalty > 0 ? 'penalty' : ''
    });

    this.setData({
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
    for (ri = 0; ri < NUM_ROWS; ri++) { newRows.push(this.data._rows[ri].slice()); }
    newRows[br].push(card);

    var marker = pi === 0 ? ' (you)' : '';
    var newLogs = this.data.logs.slice();
    newLogs.unshift({
      msg: ARENA_NAMES[pi] + marker + ' ' + card + ' -> row ' + (br+1),
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
    for (ri = 0; ri < NUM_ROWS; ri++) { newRows.push(this.data._rows[ri].slice()); }
    newRows[r] = [card];

    var newLogs = this.data.logs.slice();
    newLogs.unshift({ msg: 'You took row ' + (r+1) + ' -' + penalty + '🐂', cls: 'penalty' });

    this.setData({
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
      endTitle: winner === 0 ? "🎉 You Win!" : ARENA_NAMES[winner] + " Wins!",
      endDesc: winner === 0 ? "You beat the AIs!" : "Rank: " + humanRank + "/6",
      endRanking: ranking.map(function(pi, rank) {
        return {
          pi: pi, medal: medals[rank], name: ARENA_NAMES[pi],
          isYou: pi === 0, score: self.data.scores[pi]
        };
      }),
      actionMsg: '🏁 Game Over!'
    });
  }
});


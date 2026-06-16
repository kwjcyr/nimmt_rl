// ============================================================
//  6 Nimmt! - 多人联机页面
//  调用 kwjcyr.com PHP 后端，长轮询实现伪实时同步
// ============================================================

var API_BASE = "https://kwjcyr.com/nimmt_api/nimmt_multi.php";
var END_SCORE = 66;
var MAX_ROW = 6;

// ============================================================
//  工具函数
// ============================================================
function getBulls(c) {
  if (c === 55) return 7;
  if (c % 11 === 0) return 5;
  if (c % 10 === 0) return 3;
  if (c % 5 === 0) return 2;
  return 1;
}

function bullsStr(n) {
  var s = "";
  for (var i = 0; i < n; i++) s += "🐂";
  return s;
}

function rowBulls(row) {
  return row.reduce(function(s, c) { return s + getBulls(c); }, 0);
}

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
    for (var si = 0; si < MAX_ROW; si++) slots.push({ filled: si < row.length });
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

function buildHandData(hand, isPickPhase, selectedCard) {
  return hand.map(function(card) {
    var isSelected = (card === selectedCard);
    var extraCls = isPickPhase ? (isSelected ? "selectable selected" : "selectable") : "";
    return buildCardData(card, extraCls);
  });
}

function buildScoreItems(players, scores, playerId) {
  return players.map(function(p) {
    var pid = p.id || p.player_id;
    var s = scores[pid] || 0;
    return {
      id: pid,
      name: p.name || pid,
      score: s,
      isMe: (pid === playerId),
      danger: s >= END_SCORE * 0.7
    };
  });
}

// ============================================================
//  Page
// ============================================================
Page({
  data: {
    // 界面切换
    screen: "lobby",  // lobby | waiting | playing

    // 大厅配置
    myName: "",
    numHumans: 1,
    numAI: 3,
    totalValid: true,
    joinRoomId: "",

    // 房间状态
    roomId: "",
    playerId: "",
    players: [],
    isHost: false,
    waitingTip: "⏳ 等待玩家...",
    waitingFull: false,

    // 游戏状态
    round: 1,
    rowData: [],
    handData: [],
    scoreItems: [],
    playedCard: null,     // 我这轮已出的牌
    actionMsg: "",
    canConfirm: false,
    selectedCard: null,
    logs: [],
    logScrollTop: 99999,

    // 弹窗
    showRowModal: false,
    choosingForCard: null,
    showEndModal: false,
    endDesc: "",
    endRanking: []
  },

  // ============ 内部状态（不放入 data，避免频繁 setData） ============
  _state: {
    phase: "lobby",        // lobby | waiting | playing | pick_row | finished
    playerId: "",
    hand: [],
    rows: [],
    scores: {},
    players: [],
    selectedCard: null,
    logs: [],
    playedCards: {},
    rowsSnapshot: "",
    choosingForCard: null,
    numHumans: 2,
    numAI: 2
  },
  _pollTs: 0,
  _polling: false,
  _pollTask: null,
  _roomCheckTimer: null,
  _cardQueue: [],
  _cardPlaying: false,
  _afterCardCallbacks: [],
  _afterCheckTimer: null,

  // ============================================================
  //  生命周期
  // ============================================================
  onLoad: function() {
    // 尝试恢复上次的昵称
    var name = wx.getStorageSync("nimmt_player_name") || "";
    if (name) this.setData({ myName: name });
  },

  onUnload: function() {
    this._stopPolling();
  },

  onHide: function() {
    // 切到后台时继续轮询（不停止）
  },

  // ============================================================
  //  大厅事件
  // ============================================================
  onNameInput: function(e) {
    this.setData({ myName: e.detail.value });
  },

  onRoomIdInput: function(e) {
    this.setData({ joinRoomId: e.detail.value });
  },

  adjHumans: function(e) {
    var d = e.currentTarget.dataset.d;
    var v = Math.max(1, Math.min(6, this.data.numHumans + d));  // 允许最少1个真实玩家
    var ai = this.data.numAI;
    this.setData({
      numHumans: v,
      totalValid: (v + ai >= 2 && v + ai <= 6 && v >= 1)
    });
  },

  adjAI: function(e) {
    var d = e.currentTarget.dataset.d;
    var v = Math.max(0, Math.min(5, this.data.numAI + d));
    var h = this.data.numHumans;
    this.setData({
      numAI: v,
      totalValid: (h + v >= 2 && h + v <= 6)
    });
  },

  createRoom: function() {
    var name = (this.data.myName || "").trim();
    if (!name) {
      wx.showToast({ title: "请先输入昵称！", icon: "none" });
      return;
    }
    var nh = this.data.numHumans;
    var na = this.data.numAI;
    if (nh < 1) {
      wx.showToast({ title: "至少需要1个真实玩家", icon: "none" });
      return;
    }
    if (nh + na < 2 || nh + na > 6) {
      wx.showToast({ title: "总人数需在2~6之间", icon: "none" });
      return;
    }
    wx.setStorageSync("nimmt_player_name", name);

    var pid = "p" + Date.now().toString(36) + Math.random().toString(36).substr(2, 5);
    this._state.playerId = pid;
    this._state.myName = name;
    this._state.numHumans = nh;
    this._state.numAI = na;

    var self = this;
    wx.showLoading({ title: "创建房间..." });
    console.log("[DEBUG] createRoom url:", API_BASE);
    wx.request({
      url: API_BASE,
      method: "POST",
      header: { "Content-Type": "application/json" },
      data: JSON.stringify({
        action: "create_room",
        player_id: pid,
        player_name: name,
        num_humans: nh,
        num_ai: na
      }),
      success: function(res) {
        wx.hideLoading();
        console.log("[DEBUG] createRoom success, statusCode:", res.statusCode, "data:", JSON.stringify(res.data));
        var data = res.data;
        if (!data || data.error) {
          wx.showToast({ title: "创建失败: " + (data && data.error || "网络错误"), icon: "none" });
          return;
        }
        self._state.players = data.players || [];
        self._state.phase = "waiting";
        self.setData({
          roomId: data.room_id,
          playerId: pid,
          players: data.players || [],
          isHost: true,
          screen: "waiting"
        });
        self._updateWaitingTip();
        self._startPolling();
      },
      fail: function(err) {
        wx.hideLoading();
        console.log("[DEBUG] createRoom fail:", JSON.stringify(err));
        wx.showToast({ title: "失败: " + (err.errMsg || JSON.stringify(err)), icon: "none", duration: 4000 });
      }
    });
  },

  joinRoom: function() {
    var name = (this.data.myName || "").trim();
    if (!name) {
      wx.showToast({ title: "请先输入昵称！", icon: "none" });
      return;
    }
    var rid = (this.data.joinRoomId || "").trim();
    if (!rid) {
      wx.showToast({ title: "请输入房间号！", icon: "none" });
      return;
    }
    wx.setStorageSync("nimmt_player_name", name);

    var pid = "p" + Date.now().toString(36) + Math.random().toString(36).substr(2, 5);
    this._state.playerId = pid;
    this._state.myName = name;

    var self = this;
    wx.showLoading({ title: "加入房间..." });
    wx.request({
      url: API_BASE,
      method: "POST",
      header: { "Content-Type": "application/json" },
      data: JSON.stringify({
        action: "join_room",
        room_id: rid,
        player_id: pid,
        player_name: name
      }),
      success: function(res) {
        wx.hideLoading();
        var data = res.data;
        if (!data || data.error) {
          wx.showToast({ title: "加入失败: " + (data && data.error || "房间不存在"), icon: "none" });
          return;
        }
        self._state.players = data.players || [];
        self._state.phase = "waiting";
        if (data.num_humans) self._state.numHumans = data.num_humans;
        if (data.num_ai) self._state.numAI = data.num_ai;
        self.setData({
          roomId: data.room_id,
          playerId: pid,
          players: data.players || [],
          isHost: false,
          screen: "waiting"
        });
        self._updateWaitingTip();
        self._startPolling();
      },
      fail: function(err) {
        wx.hideLoading();
        wx.showToast({ title: "加入失败，请检查网络", icon: "none" });
      }
    });
  },

  // ============================================================
  //  等待室事件
  // ============================================================
  startGame: function() {
    if (!this.data.isHost) return;
    this._sendToServer({ type: "start_game" });
  },

  leaveRoom: function() {
    this._stopPolling();
    this._cardQueue = [];
    this._cardPlaying = false;
    this._afterCardCallbacks = [];
    this._state = {
      phase: "lobby", playerId: "", hand: [], rows: [],
      scores: {}, players: [], selectedCard: null, logs: [],
      playedCards: {}, rowsSnapshot: "", choosingForCard: null,
      numHumans: 2, numAI: 2
    };
    this._pollTs = 0;
    this.setData({
      screen: "lobby", roomId: "", playerId: "", players: [],
      isHost: false, rowData: [], handData: [], scoreItems: [],
      playedCard: null, actionMsg: "", canConfirm: false, selectedCard: null,
      logs: [], showRowModal: false, choosingForCard: null,
      showEndModal: false, endDesc: "", endRanking: []
    });
  },

  // ============================================================
  //  游戏事件
  // ============================================================
  onCardClick: function(e) {
    if (this._state.phase !== "playing") return;
    var card = e.currentTarget.dataset.card;
    this._state.selectedCard = card;
    this.setData({
      selectedCard: card,
      handData: buildHandData(this._state.hand, true, card),
      actionMsg: "已选: " + card + " (" + bullsStr(getBulls(card)) + ")  点击其他牌重新选",
      canConfirm: true
    });
  },

  confirmPlay: function() {
    var card = this._state.selectedCard;
    if (!card) return;

    // 发送到服务器
    this._sendToServer({ type: "play_card", card: card });

    // 本地乐观更新
    var idx = this._state.hand.indexOf(card);
    if (idx !== -1) this._state.hand.splice(idx, 1);
    this._addLog("你打出 " + card + " (" + bullsStr(getBulls(card)) + ")", "you");
    this._state.selectedCard = null;
    this._state.playedCards = {};
    this._state.playedCards[this._state.playerId] = card;

    this.setData({
      handData: buildHandData(this._state.hand, false, null),
      selectedCard: null,
      canConfirm: false,
      playedCard: card,
      actionMsg: "⏳ 等待其他玩家..."
    });
  },

  humanChooseRow: function(e) {
    var r = parseInt(e.currentTarget.dataset.row);
    var card = this._state.choosingForCard;
    this.setData({ showRowModal: false });
    this._sendToServer({ type: "pick_row", row: r, card: card, rows_snapshot: this._state.rowsSnapshot });
    this.setData({ actionMsg: "⏳ 结算中..." });
  },

  retryGame: function() {
    this.setData({ showEndModal: false });
    if (this.data.isHost) this.startGame();
  },

  // ============================================================
  //  长轮询
  // ============================================================
  _startPolling: function() {
    if (this._polling || !this.data.roomId) return;
    this._polling = true;
    this._pollLoop();
    // 5秒兜底检查（防止 game_over 消息丢失）
    var self = this;
    if (this._roomCheckTimer) clearInterval(this._roomCheckTimer);
    this._roomCheckTimer = setInterval(function() { self._checkRoomInfo(); }, 5000);
  },

  _stopPolling: function() {
    this._polling = false;
    if (this._roomCheckTimer) {
      clearInterval(this._roomCheckTimer);
      this._roomCheckTimer = null;
    }
    if (this._pollTask) {
      try { this._pollTask.abort(); } catch(e) {}
      this._pollTask = null;
    }
  },

  _pollLoop: function() {
    if (!this._polling || !this.data.roomId) return;
    var self = this;
    var url = API_BASE + "?action=poll&room_id=" + this.data.roomId +
              "&player_id=" + this._state.playerId + "&since=" + this._pollTs;

    var task = wx.request({
      url: url,
      method: "GET",
      timeout: 30000,
      success: function(res) {
        self._pollTask = null;
        var data = res.data;
        if (data && data.messages && data.messages.length > 0) {
          if (data.ts) self._pollTs = data.ts;
          for (var i = 0; i < data.messages.length; i++) {
            self._handleMsg(data.messages[i]);
          }
        }
        if (self._polling) {
          setTimeout(function() { self._pollLoop(); }, 200);
        }
      },
      fail: function(err) {
        self._pollTask = null;
        if (self._polling) {
          // 超时或网络错误，延迟重试
          setTimeout(function() { self._pollLoop(); }, 2000);
        }
      }
    });
    this._pollTask = task;
  },

  _checkRoomInfo: function() {
    if (!this._polling || !this.data.roomId) return;
    var phase = this._state.phase;
    if (phase !== "playing" && phase !== "pick_row") return;
    var self = this;
    wx.request({
      url: API_BASE + "?action=room_info&room_id=" + this.data.roomId,
      method: "GET",
      success: function(res) {
        var data = res.data;
        if (!data || data.error) return;
        if (data.phase === "finished" && self._state.phase !== "finished") {
          console.log("[Fallback] game finished via room_info");
          var playerList = (data.players && data.players.length > 0) ? data.players : self._state.players;
          var ranking = [];
          playerList.forEach(function(p) {
            var pid = p.id || p.player_id;
            var s = (data.scores && data.scores[pid] !== undefined) ? data.scores[pid] : (self._state.scores[pid] || 0);
            ranking.push({ name: p.name, score: s, isYou: (pid === self._state.playerId) });
          });
          ranking.sort(function(a, b) { return a.score - b.score; });
          self._state.phase = "finished";
          self._showEndModal(ranking);
          self._addLog("🏆 游戏结束！", "system");
        }
      }
    });
  },

  // ============================================================
  //  card_placed 逐张动画队列
  // ============================================================
  _enqueueCardPlaced: function(msg) {
    this._cardQueue.push(msg);
    if (!this._cardPlaying) this._drainCardPlaced();
  },

  _drainCardPlaced: function() {
    if (this._cardQueue.length === 0) {
      this._cardPlaying = false;
      // 检查等待中的回调
      this._flushAfterCallbacks();
      return;
    }
    this._cardPlaying = true;
    var msg = this._cardQueue.shift();

    // 应用这一张牌的状态
    if (msg.log_msg) this._addLog(msg.log_msg, msg.log_cls || "");
    if (msg.rows) this._state.rows = msg.rows;
    if (msg.scores) this._state.scores = msg.scores;

    this.setData({
      rowData: buildRowData(this._state.rows),
      scoreItems: buildScoreItems(this._state.players, this._state.scores, this._state.playerId)
    });

    var self = this;
    setTimeout(function() { self._drainCardPlaced(); }, 350);
  },

  _afterCardPlaced: function(fn) {
    if (!this._cardPlaying && this._cardQueue.length === 0) {
      fn();
    } else {
      this._afterCardCallbacks.push(fn);
    }
  },

  _flushAfterCallbacks: function() {
    if (this._cardPlaying || this._cardQueue.length > 0) return;
    var cbs = this._afterCardCallbacks.slice();
    this._afterCardCallbacks = [];
    for (var i = 0; i < cbs.length; i++) {
      cbs[i]();
    }
  },

  // ============================================================
  //  消息处理
  // ============================================================
  _handleMsg: function(msg) {
    console.log("[Poll] recv:", msg.type);
    var self = this;

    switch (msg.type) {

      case "room_joined":
        this._state.players = msg.players || [];
        this._state.phase = msg.phase || "waiting";
        if (msg.num_humans) this._state.numHumans = msg.num_humans;
        if (msg.num_ai) this._state.numAI = msg.num_ai;
        this.setData({ players: this._state.players });
        this._updateWaitingTip();
        break;

      case "player_joined":
        this._state.players = msg.players || [];
        if (msg.num_humans) this._state.numHumans = msg.num_humans;
        if (msg.num_ai) this._state.numAI = msg.num_ai;
        this.setData({ players: this._state.players });
        this._updateWaitingTip();
        this._addLog((msg.name || "玩家") + " 加入了房间！", "system");
        if (this.data.isHost) {
          var realCount = this._state.players.filter(function(p) { return !p.is_ai; }).length;
          // 若等待室满了自动可以开始（房主看到提示）
          this._updateWaitingTip();
        }
        break;

      case "player_left":
        this._state.players = this._state.players.filter(function(p) { return p.id !== msg.player_id; });
        this.setData({ players: this._state.players });
        this._updateWaitingTip();
        break;

      case "error":
        wx.showToast({ title: "错误: " + (msg.detail || "未知"), icon: "none" });
        break;

      case "pong":
        break;

      case "game_start":
        this._state.phase = "playing";
        this._state.hand = msg.hand || [];
        this._state.rows = msg.rows || [];
        this._state.round = msg.round || 1;
        this._state.scores = {};
        this._state.selectedCard = null;
        this._state.logs = [{ msg: "🎮 游戏开始！", cls: "system" }];
        this._state.playedCards = {};
        if (msg.players) {
          this._state.players = msg.players.map(function(p) { return { id: p.id, name: p.name, is_ai: p.is_ai }; });
          for (var i = 0; i < msg.players.length; i++) {
            this._state.scores[msg.players[i].id] = msg.players[i].score || 0;
          }
        }
        this.setData({
          screen: "playing",
          round: this._state.round,
          rowData: buildRowData(this._state.rows),
          handData: buildHandData(this._state.hand, true, null),
          scoreItems: buildScoreItems(this._state.players, this._state.scores, this._state.playerId),
          playedCard: null,
          selectedCard: null,
          canConfirm: false,
          actionMsg: "👆 点击你的手牌选一张出",
          logs: this._state.logs,
          logScrollTop: 99999,
          showRowModal: false,
          showEndModal: false
        });
        break;

      case "game_started":
        // 非房主收到广播，不需要额外处理（房主已收到 game_start）
        break;

      case "card_played":
        this.setData({ actionMsg: "⏳ " + (msg.player_name || "玩家") + " 已出牌 (剩余 " + msg.remaining + ")" });
        if (msg.played_cards && msg.played_cards[this._state.playerId] !== undefined) {
          this._state.playedCards = {};
          this._state.playedCards[this._state.playerId] = msg.played_cards[this._state.playerId];
          this.setData({ playedCard: msg.played_cards[this._state.playerId] });
        }
        break;

      case "card_placed":
        this._enqueueCardPlaced(msg);
        break;

      case "must_pick_row":
        (function(m) {
          self._afterCardPlaced(function() {
            self._state.phase = "pick_row";
            self._state.choosingForCard = m.card;
            self._state.rows = m.rows || self._state.rows;
            self._state.rowsSnapshot = m.rows_snapshot || "";
            self.setData({
              showRowModal: true,
              choosingForCard: m.card,
              rowData: buildRowData(self._state.rows),
              actionMsg: "😱 你的牌 " + m.card + " 最小，请选择要收走的一列！"
            });
          });
        })(msg);
        break;

      case "round_end":
        (function(m) {
          self._afterCardPlaced(function() {
            self._state.round = m.round;
            self._state.rows = m.rows || [];
            self._state.scores = m.scores || self._state.scores;
            if (self._state.logs.length === 0 && m.logs && m.logs.length > 0) {
              self._state.logs = m.logs.slice().reverse();
              if (self._state.logs.length > 60) self._state.logs = self._state.logs.slice(0, 60);
            }
            self._state.phase = "playing";
            self._state.selectedCard = null;
            self._state.playedCards = {};
            self.setData({
              round: m.round,
              rowData: buildRowData(self._state.rows),
              scoreItems: buildScoreItems(self._state.players, self._state.scores, self._state.playerId),
              handData: buildHandData(self._state.hand, true, null),
              selectedCard: null,
              canConfirm: false,
              playedCard: null,
              actionMsg: "🃏 第 " + m.round + " 轮 — 点击你的牌出牌",
              logs: self._state.logs,
              logScrollTop: 99999
            });
          });
        })(msg);
        break;

      case "row_picked":
        (function(m) {
          self._afterCardPlaced(function() {
            self._state.rows = m.rows || [];
            self._state.scores = m.scores || self._state.scores;
            self._state.phase = "playing";
            self.setData({
              showRowModal: false,
              rowData: buildRowData(self._state.rows),
              scoreItems: buildScoreItems(self._state.players, self._state.scores, self._state.playerId)
            });
            if (m.auto) {
              var autoPlayer = self._state.players.find(function(p) { return p.id === m.player_id; });
              var autoName = (autoPlayer && autoPlayer.name) || m.player_id;
              self._addLog("⏰ " + autoName + " 超时，AI自动代选第" + (m.row + 1) + "列", "warn");
            }
          });
        })(msg);
        break;

      case "game_over":
        (function(m) {
          self._afterCardPlaced(function() {
            self._state.phase = "finished";
            if (self._state.logs.length === 0 && m.logs && m.logs.length > 0) {
              self._state.logs = m.logs.slice().reverse();
              if (self._state.logs.length > 60) self._state.logs = self._state.logs.slice(0, 60);
            }
            self._addLog("🏆 游戏结束！", "system");
            self._showEndModal(m.ranking || []);
          });
        })(msg);
        break;
    }
  },

  // ============================================================
  //  发送消息到服务器
  // ============================================================
  _sendToServer: function(msg) {
    var payload = {
      action: "send",
      type: msg.type,
      room_id: this.data.roomId,
      player_id: this._state.playerId
    };
    if (msg.card !== undefined) payload.card = msg.card;
    if (msg.row !== undefined) payload.row = msg.row;
    if (msg.rows_snapshot !== undefined) payload.rows_snapshot = msg.rows_snapshot;
    wx.request({
      url: API_BASE,
      method: "POST",
      header: { "Content-Type": "application/json" },
      data: JSON.stringify(payload),
      fail: function(err) { console.error("[Send] error:", err); }
    });
  },

  // ============================================================
  //  辅助：更新等待室提示
  // ============================================================
  _updateWaitingTip: function() {
    var players = this._state.players;
    var numHumans = this._state.numHumans;
    var realCount = players.filter(function(p) { return !p.is_ai; }).length;
    var aiCount = players.filter(function(p) { return !!p.is_ai; }).length;
    var isFull = realCount >= numHumans;
    var tip;
    if (isFull) {
      tip = "✅ 人已到齐！(" + realCount + "人+" + aiCount + "🤖) 即将开始...";
    } else {
      tip = "⏳ 等待玩家加入... (" + realCount + "/" + numHumans + ") 🤖AI: " + aiCount + "人";
    }
    this.setData({ waitingTip: tip, waitingFull: isFull });
  },

  // ============================================================
  //  辅助：添加日志
  // ============================================================
  _addLog: function(msg, cls) {
    this._state.logs.unshift({ msg: msg, cls: cls || "" });
    if (this._state.logs.length > 60) this._state.logs.pop();
    this.setData({ logs: this._state.logs, logScrollTop: 99999 });
  },

  // ============================================================
  //  辅助：显示结算弹窗
  // ============================================================
  _showEndModal: function(ranking) {
    var medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣"];
    var myIdx = -1;
    for (var i = 0; i < ranking.length; i++) {
      if (ranking[i].isYou) { myIdx = i; break; }
    }
    var endDesc = myIdx === 0 ? "🏆 你是最强的！牛头最少！" : (myIdx >= 0 ? "你排名第 " + (myIdx + 1) + " 名" : "");
    var endRanking = ranking.map(function(r, i) {
      return {
        medal: medals[i] || (i + 1) + ".",
        name: r.name,
        score: r.score,
        isYou: !!r.isYou
      };
    });
    this.setData({
      showEndModal: true,
      endDesc: endDesc,
      endRanking: endRanking,
      logs: this._state.logs
    });
  }
});


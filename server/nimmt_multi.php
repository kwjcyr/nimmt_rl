<?php
/**
 * Nimmt! Multi-user Server - Fixed version (Async row pick fixed)
 * PHP 5.3 compatible - uses array() instead of []
 */
header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type');

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') exit(0);

$DATA_DIR = '/opt/nimmt/data/';
$POLL_TIMEOUT = 25;
$ROOM_TTL = 1800;
@mkdir($DATA_DIR, 0777, true);

define('MAX_ROW', 6);
define('END_SCORE', 66);

// ============================================================
// Room file helpers
// ============================================================
function cleanupStaleRooms() {
    global $DATA_DIR, $ROOM_TTL;
    $now = microtime(true);
    $files = glob($DATA_DIR . 'room_*.json');
    $removed = 0;
    foreach ($files as $f) {
        if (filemtime($f) < ($now - $ROOM_TTL)) {
            @unlink($f);
            $removed++;
        }
    }
    return $removed;
}

function getDataFile($rid) {
    global $DATA_DIR;
    return $DATA_DIR . 'room_' . preg_replace('/[^a-zA-Z0-9]/', '', $rid) . '.json';
}

function readRoom($rid) {
    $f = getDataFile($rid);
    if (!file_exists($f)) return null;
    $fp = fopen($f, 'r');
    if (!$fp) return null;
    flock($fp, LOCK_SH);
    $json = stream_get_contents($fp);
    flock($fp, LOCK_UN);
    fclose($fp);
    return $json ? json_decode($json, true) : null;
}

function writeRoom($rid, $d) {
    $now = microtime(true);
    foreach ($d['messages'] as &$m) {
        if (!isset($m['_ts'])) $m['_ts'] = $now;
        if (!isset($m['_delivered'])) $m['_delivered'] = array();
    }
    unset($m);
    $f = getDataFile($rid);
    $fp = fopen($f, 'c');
    if (!$fp) return;
    flock($fp, LOCK_EX);
    ftruncate($fp, 0);
    fseek($fp, 0);
    fwrite($fp, json_encode($d));
    flock($fp, LOCK_UN);
    fclose($fp);
}

/**
 * 专门用于 poll：原子读-改-写，仅更新 messages 里的 _delivered 标记和清理已送达消息。
 * 避免 poll 进程用旧版 room 数据整体覆盖由 send 进程写入的最新 rows/chosen/logs 等。
 * 返回此次应发给 $pid 的新消息数组。
 */
function atomicDeliverMessages($rid, $pid, $since) {
    $f = getDataFile($rid);
    if (!file_exists($f)) return array(null, array());
    $fp = fopen($f, 'c+');
    if (!$fp) return array(null, array());
    flock($fp, LOCK_EX);
    $json = stream_get_contents($fp);
    $room = $json ? json_decode($json, true) : null;
    if (!$room) { flock($fp, LOCK_UN); fclose($fp); return array(null, array()); }

    $now = microtime(true);
    $needWrite = false;

    // 1. 更新当前玩家心跳（last_active 按玩家维度）
    if (!isset($room['heartbeat'])) $room['heartbeat'] = array();
    $room['heartbeat'][$pid] = $now;
    $needWrite = true;

    // 2. 检查 pending_pick 是否超时（30s），超时则 AI 代选
    if (isset($room['pending_pick'])) {
        $pendingAge = $now - (isset($room['pending_pick']['created_at']) ? $room['pending_pick']['created_at'] : 0);
        if ($pendingAge >= 30) {
            checkPendingPickTimeout($room, $rid);
            $needWrite = true;
        }
    }

    $humanPids = array();
    foreach ($room['players'] as $p) {
        if (!$p['is_ai']) $humanPids[] = $p['player_id'];
    }

    $newMsgs = array();
    foreach ($room['messages'] as $mi => &$m) {
        // game_over/round_end 关键消息：即使时间戳 <= since 也要检查是否已送达
        $isCritical = isset($m['type']) && ($m['type'] === 'game_over' || $m['type'] === 'round_end');
        if (!$isCritical && (!isset($m['_ts']) || $m['_ts'] <= $since)) continue;
        $targetOk = !isset($m['target_pid']) || $m['target_pid'] == $pid;
        if (!$targetOk) continue;
        if (!isset($m['_delivered'])) $m['_delivered'] = array();
        if (in_array($pid, $m['_delivered'])) continue;

        $outMsg = $m;
        unset($outMsg['_delivered'], $outMsg['_ts']);
        $newMsgs[] = $outMsg;
        $m['_delivered'][] = $pid;
        $needWrite = true;
    }
    unset($m);

    if ($needWrite) {
        $cleanupNow = microtime(true);
        $room['messages'] = array_values(array_filter($room['messages'], function($m) use ($humanPids, $cleanupNow) {
            // game_over 消息被 delivered 后仍保留 120 秒，防止前端 poll 时差错过
            $isGameOver = (isset($m['type']) && $m['type'] === 'game_over');
            $msgAge = $cleanupNow - (isset($m['_ts']) ? $m['_ts'] : 0);
            if (!isset($m['target_pid'])) {
                $delivered = isset($m['_delivered']) ? $m['_delivered'] : array();
                foreach ($humanPids as $hp) {
                    if (!in_array($hp, $delivered)) return true;
                }
                // 全部已送达：game_over 类消息额外保留 120s
                return $isGameOver && $msgAge < 120;
            } else {
                $delivered = isset($m['_delivered']) ? $m['_delivered'] : array();
                $allDone = in_array($m['target_pid'], $delivered);
                // 已送达：game_over 类消息额外保留 120s
                return !$allDone || ($isGameOver && $msgAge < 120);
            }
        }));
        ftruncate($fp, 0);
        fseek($fp, 0);
        fwrite($fp, json_encode($room));
    }
    flock($fp, LOCK_UN);
    fclose($fp);
    return array($room, $newMsgs);
}

// 获取房间内所有人类玩家ID列表
function getHumanPids($room) {
    $pids = array();
    foreach ($room['players'] as $p) {
        if (!$p['is_ai']) $pids[] = $p['player_id'];
    }
    return $pids;
}

// ============================================================
// Game rules helpers
// ============================================================
function getBulls($c) {
    if ($c == 55) return 7;
    if ($c % 11 == 0) return 5;
    if ($c % 10 == 0) return 3;
    if ($c % 5 == 0) return 2;
    return 1;
}

function rowBulls($row) {
    $s = 0;
    foreach ($row as $c) $s += getBulls($c);
    return $s;
}

function findBestRow($rows, $card) {
    $best = -1;
    $md = 1e9;
    foreach ($rows as $r => $row) {
        $t = $row[count($row)-1];
        if ($t < $card && ($card - $t) < $md) {
            $md = $card - $t;
            $best = $r;
        }
    }
    return $best;
}

function aiChooseCard($hand, $rows, $strategy) {
    if (empty($hand)) return null;
    if ($strategy === 'random') return $hand[array_rand($hand)];
    $scored = array();
    foreach ($hand as $card) {
        $br = findBestRow($rows, $card);
        if ($br === -1) {
            $min = PHP_INT_MAX;
            foreach ($rows as $r) $min = min($min, rowBulls($r));
            $risk = $min + 80;
        } elseif (count($rows[$br]) >= MAX_ROW) {
            $risk = rowBulls($rows[$br]) + 40;
        } else {
            $risk = count($rows[$br]) * 3;
        }
        $scored[] = array('card' => $card, 'risk' => $risk + (mt_rand() / mt_getrandmax() - 0.5) * 10);
    }
    usort($scored, function($a,$b){ return $a['risk'] - $b['risk']; });
    if ($strategy === 'safe') return $scored[0]['card'];
    $r = mt_rand() / mt_getrandmax();
    if ($r < 0.70) {
        $p = array_slice($scored, 0, max(1, count($scored)/3));
        return $p[array_rand($p)]['card'];
    } elseif ($r < 0.90) {
        $m = floor(count($scored)/2);
        $p = array_slice($scored, max(0,$m-1), $m+2);
        $p = array_values(array_filter($p));
        return $p[array_rand($p)]['card'];
    }
    return $hand[count($hand)-1];
}

function aiChooseRow($rows) {
    $b = PHP_INT_MAX;
    $bi = 0;
    foreach ($rows as $i => $r) {
        $rb = rowBulls($r);
        if ($rb < $b) {
            $b = $rb;
            $bi = $i;
        }
    }
    return $bi;
}

function deal($numPlayers) {
    $deck = range(1, 100);
    shuffle($deck);
    $hands = array();
    for ($i = 0; $i < $numPlayers; $i++) {
        $hands[] = array_slice($deck, $i*10, 10);
    }
    foreach ($hands as &$h) sort($h);
    $baseCards = array_slice($deck, $numPlayers*10, 5);
    sort($baseCards);
    $rows = array();
    foreach ($baseCards as $c) $rows[] = array($c);
    return array('hands' => $hands, 'rows' => $rows);
}

function playerList($room) {
    return array_map(function($p) {
        return array('id' => $p['player_id'], 'name' => $p['name'], 'is_ai' => $p['is_ai']);
    }, $room['players']);
}

// ============================================================
// Round processing with async row picks
// ============================================================
function startRoundProcessing(&$room, $rid) {
    $cards = array();
    foreach ($room['chosen'] as $pid => $card) {
        $cards[] = array('pid' => $pid, 'card' => $card);
    }
    usort($cards, function($a, $b) { return $a['card'] - $b['card']; });
    $room['round_queue'] = $cards;
    $room['round_queue_index'] = 0;
    $room['round_processing'] = true;
    processNextCard($room, $rid);
}

function processNextCard(&$room, $rid) {
    if (!$room['round_processing']) return;
    $idx = $room['round_queue_index'];
    if ($idx >= count($room['round_queue'])) {
        finishRound($room, $rid);
        return;
    }
    $item = $room['round_queue'][$idx];
    $pid = $item['pid'];
    $card = $item['card'];

    $player = null;
    foreach ($room['players'] as &$p) {
        if ($p['player_id'] == $pid) { $player = &$p; break; }
    }
    unset($p);  // 必须 unset，防止悬空引用导致后续 foreach 写乱数组
    if (!$player) {
        $room['round_queue_index']++;
        processNextCard($room, $rid);
        return;
    }

    $bestRow = findBestRow($room['rows'], $card);
    if ($bestRow === -1) {
        // 必须拿列
        if (!$player['is_ai']) {
            $room['pending_pick'] = array(
                'player_id' => $pid,
                'card' => $card,
                'queue_index' => $idx,
                'rows_snapshot' => $room['rows'],
                'created_at' => microtime(true)  // 超时检测用
            );
            $room['messages'][] = array(
                'type' => 'must_pick_row',
                'target_pid' => $pid,
                'card' => $card,
                'rows' => $room['rows'],
                'rows_snapshot' => json_encode($room['rows']),
            );
            return; // 等待前端
        } else {
            $chosenRow = aiChooseRow($room['rows']);
            applyTakeRow($room, $player, $chosenRow, $card);
            $room['round_queue_index']++;
            processNextCard($room, $rid);
            return;
        }
    } elseif (count($room['rows'][$bestRow]) >= MAX_ROW) {
        // 列已满，必须拿走
        applyTakeRow($room, $player, $bestRow, $card);
        $room['round_queue_index']++;
        processNextCard($room, $rid);
        return;
    } else {
        // 正常放入
        $room['rows'][$bestRow][] = $card;
        $logMsg = $player['name'] . ' 打出 ' . $card . ' → 第' . ($bestRow+1) . '列';
        $room['logs'][] = array('msg' => $logMsg, 'cls' => '');
        // 广播结算动作，让所有玩家前端实时 addLog
        $room['messages'][] = array(
            'type' => 'card_placed',
            'player_id' => $pid,
            'player_name' => $player['name'],
            'card' => $card,
            'row' => $bestRow,
            'rows' => $room['rows'],
            'log_msg' => $logMsg,
            'log_cls' => ''
        );
        $room['round_queue_index']++;
        processNextCard($room, $rid);
        return;
    }
}

function applyTakeRow(&$room, &$player, $rowIndex, $card) {
    $penalty = rowBulls($room['rows'][$rowIndex]);
    $player['score'] += $penalty;
    $logMsg = $player['name'] . ' 收走第' . ($rowIndex+1) . '列 (-' . $penalty . "\xF0\x9F\x90\x82)";
    $room['logs'][] = array('msg' => $logMsg, 'cls' => 'penalty');
    $room['rows'][$rowIndex] = array($card);
    // 广播收牌动作
    $sd = array();
    foreach ($room['players'] as $p) $sd[$p['player_id']] = $p['score'];
    $room['messages'][] = array(
        'type' => 'card_placed',
        'player_id' => $player['player_id'],
        'player_name' => $player['name'],
        'card' => $card,
        'row' => $rowIndex,
        'took_row' => true,
        'penalty' => $penalty,
        'rows' => $room['rows'],
        'scores' => $sd,
        'log_msg' => $logMsg,
        'log_cls' => 'penalty'
    );
}

function handlePickRow(&$room, $rid, $pid, $chosenRow, $card, $rowsSnapshotJson) {
    if (!isset($room['pending_pick']) || $room['pending_pick']['player_id'] != $pid) return false;
    $pending = $room['pending_pick'];
    if ($pending['card'] != $card) return false;

    $player = null;
    foreach ($room['players'] as &$p) {
        if ($p['player_id'] == $pid) { $player = &$p; break; }
    }
    unset($p);  // 必须 unset，防止悬空引用导致后续 foreach 写乱数组
    if (!$player) return false;

    applyTakeRow($room, $player, $chosenRow, $card);
    $room['round_queue_index'] = $pending['queue_index'] + 1;
    unset($room['pending_pick']);

    $sd = array();
    foreach ($room['players'] as $p) $sd[$p['player_id']] = $p['score'];
    $room['messages'][] = array('type' => 'row_picked', 'player_id' => $pid, 'row' => $chosenRow, 'rows' => $room['rows'], 'scores' => $sd);

    processNextCard($room, $rid);
    return true;
}

/**
 * 检查 pending_pick 是否超时，若超时则 AI 自动代选最低罚分列，继续结算。
 * 在每次 poll 进入锁区域时调用，无副作用（若无超时则直接返回）。
 */
function checkPendingPickTimeout(&$room, $rid) {
    if (!isset($room['pending_pick'])) return;
    $PICK_TIMEOUT = 30; // 秒
    $pending = $room['pending_pick'];
    $age = microtime(true) - (isset($pending['created_at']) ? $pending['created_at'] : 0);
    if ($age < $PICK_TIMEOUT) return;

    // 超时：AI 代选最低罚分列
    $pid = $pending['player_id'];
    $card = $pending['card'];
    $player = null;
    foreach ($room['players'] as &$p) {
        if ($p['player_id'] == $pid) { $player = &$p; break; }
    }
    unset($p);
    if (!$player) { unset($room['pending_pick']); return; }

    $chosenRow = aiChooseRow($room['rows']);
    applyTakeRow($room, $player, $chosenRow, $card);
    $sd = array();
    foreach ($room['players'] as $pp) $sd[$pp['player_id']] = $pp['score'];
    $room['messages'][] = array(
        'type' => 'row_picked',
        'player_id' => $pid,
        'row' => $chosenRow,
        'rows' => $room['rows'],
        'scores' => $sd,
        'auto' => true  // 标记为自动代选
    );
    $room['round_queue_index'] = $pending['queue_index'] + 1;
    unset($room['pending_pick']);
    processNextCard($room, $rid);
}

function finishRound(&$room, $rid) {
    $room['round']++;
    $room['chosen'] = array();
    $room['round_queue'] = array();
    $room['round_queue_index'] = 0;
    $room['round_processing'] = false;
    unset($room['pending_pick']);

    $handsEmpty = true;
    $someoneMax = false;
    foreach ($room['players'] as $p) {
        if (!empty($p['hand'])) $handsEmpty = false;
        if ($p['score'] >= END_SCORE) $someoneMax = true;
    }
    if ($handsEmpty || $someoneMax) {
        $room['phase'] = 'finished';
        usort($room['players'], function($a,$b){ return $a['score'] - $b['score']; });
        $rk = array();
        foreach ($room['players'] as $p) {
            $rk[] = array('name' => $p['name'], 'score' => $p['score'], 'player_id' => $p['player_id']);
        }
        // 先取 logs 快照，再清空，防止无限膨胀
        $logsSlice = array_slice($room['logs'], -20);
        $room['logs'] = array();
        // 向每个人类玩家发送各自的定向 game_over，isYou 由 player_id 决定
        foreach ($room['players'] as $p) {
            if ($p['is_ai']) continue;
            $myRk = array();
            foreach ($rk as $r) {
                $myRk[] = array('name' => $r['name'], 'score' => $r['score'], 'isYou' => ($r['player_id'] === $p['player_id']));
            }
            $room['messages'][] = array(
                'type' => 'game_over',
                'target_pid' => $p['player_id'],
                'ranking' => $myRk,
                'logs' => $logsSlice
            );
        }
    } else {
        $sd = array();
        foreach ($room['players'] as $p) $sd[$p['player_id']] = $p['score'];
        // 先取 logs 快照打入消息，再清空，避免无限膨胀
        $logsForMsg = array_slice($room['logs'], -20);
        $room['logs'] = array();
        $room['messages'][] = array('type' => 'round_end', 'round' => $room['round'], 'rows' => $room['rows'], 'scores' => $sd, 'logs' => $logsForMsg);
        autoPlayAI($room);
    }
    $room['last_update'] = microtime(true);
}

function autoPlayAI(&$room) {
    foreach ($room['players'] as &$p) {
        if ($p['is_ai'] && !empty($p['hand'])) {
            $strategy = isset($p['strategy']) ? $p['strategy'] : 'greedy';
            $ac = aiChooseCard($p['hand'], $room['rows'], $strategy);
            if ($ac !== null) {
                $room['chosen'][$p['player_id']] = $ac;
                $k = array_search($ac, $p['hand']);
                if ($k !== false) array_splice($p['hand'], $k, 1);
            }
        }
    }
    unset($p);
}

function startGameForRoom(&$room) {
    $total = count($room['players']);
    $d = deal($total);
    $room['rows'] = $d['rows'];
    $room['round'] = 1;
    $room['chosen'] = array();
    $room['phase'] = 'playing';
    $room['logs'] = array();
    $room['round_queue'] = array();
    $room['round_queue_index'] = 0;
    $room['round_processing'] = false;
    unset($room['pending_pick']);

    for ($i = 0; $i < $total; $i++) {
        $room['players'][$i]['hand'] = $d['hands'][$i];
        $room['players'][$i]['score'] = 0;
    }
    foreach ($room['players'] as $p) {
        if (!$p['is_ai']) {
            $room['messages'][] = array(
                'type' => 'game_start',
                'target_pid' => $p['player_id'],
                'hand' => $p['hand'],
                'rows' => $d['rows'],
                'round' => 1,
                'players' => array_map(function($pp) {
                    return array('id' => $pp['player_id'], 'name' => $pp['name'], 'score' => $pp['score'], 'is_ai' => $pp['is_ai']);
                }, $room['players'])
            );
        }
    }
    $room['messages'][] = array('type' => 'game_started', 'players' => playerList($room));
    autoPlayAI($room);
}

// ============================================================
// Main routing
// ============================================================
$input = json_decode(file_get_contents('php://input'), true);
if (!$input) $input = $_POST;
$action = isset($_REQUEST['action']) ? $_REQUEST['action'] : '';
if (!$action && isset($input['action'])) $action = $input['action'];

if (in_array($action, array('create_room','join_room','poll')) && mt_rand(1,100)<=5) cleanupStaleRooms();

switch ($action) {
    case 'create_room':
        $pid = isset($input['player_id']) ? $input['player_id'] : '';
        $pname = isset($input['player_name']) ? $input['player_name'] : ('P'.mt_rand(1,999));
        $numHumans = intval(isset($input['num_humans']) ? $input['num_humans'] : 2);
        $numAI = intval(isset($input['num_ai']) ? $input['num_ai'] : 0);
        if (!$pid) { echo json_encode(array('error'=>'player_id required')); exit(); }
        $total = $numHumans + $numAI;
        if ($total < 2 || $total > 6) { echo json_encode(array('error'=>'Total players must be 2~6')); exit(); }
        if ($numHumans < 1) { echo json_encode(array('error'=>'Need at least 1 human player')); exit(); }
        $rid = sprintf('%06d', mt_rand(0, 999999));
        $aiNames = array("\xF0\x9F\x90\x82 \xe5\x91\xa8\xe8\x83\x96\xe5\xad\x90\xe5\x85\xbb\xe7\x9a\x8480\xe5\xa4\xb4\xe7\x89\x9b", "\xF0\x9F\xAB\x8F \xe9\xa9\xb4\xe5\x85\x88\xe7\x94\x9f\xe5\x8a\xaa\xe5\x8a\x9b\xe5\x85\xbb\xe7\x89\x9b", "\xF0\x9F\x8D\xB7 \xe6\x9d\x8e\xe5\xa5\xb3\xe5\xa3\xab\xe8\xaf\xb7\xe7\xbb\xa7\xe7\xbb\xad\xe5\x96\x9d", "\xF0\x9F\x90\x9F rb\xe4\xbd\xa0\xe5\x85\xbb\xe9\xb1\xbc\xe5\x91\xa2", "\xF0\x9F\x90\xa5 \xe4\xb8\x80\xe7\xbe\xa4\xe5\xb0\x8f\xe8\x8f\x9c\xe9\xb8\xa1");
        $aiStrategies = array('greedy', 'safe', 'random', 'greedy', 'safe');
        $players = array(array('player_id' => $pid, 'name' => $pname, 'is_ai' => false, 'score' => 0, 'hand' => array()));
        for ($i = 0; $i < $numAI; $i++) {
            $players[] = array(
                'player_id' => 'ai_' . $rid . '_' . $i,
                'name' => $aiNames[$i % count($aiNames)],
                'is_ai' => true,
                'score' => 0,
                'hand' => array(),
                'strategy' => $aiStrategies[$i % count($aiStrategies)]
            );
        }
        $room = array(
            'room_id' => $rid,
            'name' => 'Room ' . substr($rid, 0, 6),
            'num_humans' => $numHumans,
            'num_ai' => $numAI,
            'max_players' => $total,
            'host_id' => $pid,
            'players' => $players,
            'phase' => 'waiting',
            'rows' => array(),
            'round' => 1,
            'chosen' => array(),
            'logs' => array(),
            'messages' => array(),
            'last_update' => microtime(true)
        );
        writeRoom($rid, $room);
        echo json_encode(array(
            'room_id' => $rid,
            'name' => $room['name'],
            'players' => playerList($room),
            'num_humans' => $numHumans,
            'num_ai' => $numAI,
            'max_players' => $room['max_players']
        ));
        break;

    case 'join_room':
        $rid = isset($input['room_id']) ? $input['room_id'] : '';
        $pid = isset($input['player_id']) ? $input['player_id'] : '';
        $pname = isset($input['player_name']) ? $input['player_name'] : ('P'.mt_rand(1,999));
        if (!$rid || !$pid) { echo json_encode(array('error'=>'room_id and player_id required')); exit(); }
        $fjoin = getDataFile($rid);
        if (!file_exists($fjoin)) { echo json_encode(array('error'=>'Room not found')); exit(); }
        $fpjoin = fopen($fjoin, 'c+');
        if (!$fpjoin) { echo json_encode(array('error'=>'Room not found')); exit(); }
        flock($fpjoin, LOCK_EX);
        $jjoin = stream_get_contents($fpjoin);
        $room = $jjoin ? json_decode($jjoin, true) : null;
        if (!$room) { flock($fpjoin, LOCK_UN); fclose($fpjoin); echo json_encode(array('error'=>'Room not found')); exit(); }
        $realCount = 0;
        foreach ($room['players'] as $p) if (!$p['is_ai']) $realCount++;
        if ($realCount >= $room['num_humans']) {
            $exists = false;
            foreach ($room['players'] as $p) if ($p['player_id'] == $pid) $exists = true;
            if (!$exists) { flock($fpjoin, LOCK_UN); fclose($fpjoin); echo json_encode(array('error'=>'Room is full (no more human slots)')); exit(); }
        }
        $exists = false;
        foreach ($room['players'] as $p) if ($p['player_id'] == $pid) $exists = true;
        if (!$exists) {
            $room['players'][] = array('player_id' => $pid, 'name' => $pname, 'is_ai' => false, 'score' => 0, 'hand' => array());
            $realCount++;
        }
        $room['messages'][] = array(
            'type' => 'player_joined',
            'player_id' => $pid,
            'name' => $pname,
            'players' => playerList($room),
            'num_humans' => $room['num_humans'],
            'num_ai' => $room['num_ai']
        );
        $room['last_update'] = microtime(true);
        $nowjoin = microtime(true);
        foreach ($room['messages'] as &$mjoin) {
            if (!isset($mjoin['_ts'])) $mjoin['_ts'] = $nowjoin;
            if (!isset($mjoin['_delivered'])) $mjoin['_delivered'] = array();
        }
        unset($mjoin);
        ftruncate($fpjoin, 0); fseek($fpjoin, 0);
        fwrite($fpjoin, json_encode($room));
        flock($fpjoin, LOCK_UN);
        fclose($fpjoin);
        echo json_encode(array(
            'room_id' => $rid,
            'name' => $room['name'],
            'players' => playerList($room),
            'num_humans' => $room['num_humans'],
            'num_ai' => $room['num_ai'],
            'max_players' => $room['max_players'],
            'phase' => $room['phase']
        ));
        break;

    case 'send':
        $msgType = isset($input['type']) ? $input['type'] : '';
        $rid = isset($input['room_id']) ? $input['room_id'] : '';
        $pid = isset($input['player_id']) ? $input['player_id'] : '';

        if ($msgType === 'start_game') {
            // start_game 加独占锁
            $fsend = getDataFile($rid);
            $fpsend = fopen($fsend, 'c+');
            if (!$fpsend) { echo json_encode(array('error'=>'Room not found')); exit(); }
            flock($fpsend, LOCK_EX);
            $jsend = stream_get_contents($fpsend);
            $room = $jsend ? json_decode($jsend, true) : null;
            if (!$room) { flock($fpsend, LOCK_UN); fclose($fpsend); echo json_encode(array('error'=>'Room not found')); exit(); }
            if ($room['host_id'] !== $pid) { flock($fpsend, LOCK_UN); fclose($fpsend); echo json_encode(array('error'=>'Only host can start')); exit(); }
            if ($room['phase'] !== 'waiting') { flock($fpsend, LOCK_UN); fclose($fpsend); echo json_encode(array('error'=>'Game already started')); exit(); }
            startGameForRoom($room);
            $nowsend = microtime(true);
            foreach ($room['messages'] as &$msend) {
                if (!isset($msend['_ts'])) $msend['_ts'] = $nowsend;
                if (!isset($msend['_delivered'])) $msend['_delivered'] = array();
            }
            unset($msend);
            ftruncate($fpsend, 0); fseek($fpsend, 0);
            fwrite($fpsend, json_encode($room));
            flock($fpsend, LOCK_UN);
            fclose($fpsend);
            echo json_encode(array('ok'=>true));
        }
        elseif ($msgType === 'play_card') {
            $card = intval($input['card']);
            // play_card 加独占锁，原子读-改-写
            $fsend = getDataFile($rid);
            $fpsend = fopen($fsend, 'c+');
            if (!$fpsend) { echo json_encode(array('error'=>'Room not found')); exit(); }
            flock($fpsend, LOCK_EX);
            $jsend = stream_get_contents($fpsend);
            $room = $jsend ? json_decode($jsend, true) : null;
            if (!$room) { flock($fpsend, LOCK_UN); fclose($fpsend); echo json_encode(array('error'=>'Room not found')); exit(); }
            if ($room['phase'] !== 'playing' || isset($room['chosen'][$pid])) {
                flock($fpsend, LOCK_UN); fclose($fpsend);
                echo json_encode(array('ok'=>true)); exit();
            }
            $room['chosen'][$pid] = $card;
            $pname = '';
            foreach ($room['players'] as &$p) {
                if ($p['player_id'] == $pid) {
                    $k = array_search($card, $p['hand']);
                    if ($k !== false) array_splice($p['hand'], $k, 1);
                    $pname = $p['name'];
                    break;
                }
            }
            unset($p);
            $remain = 0;
            foreach ($room['players'] as $p) {
                if (!$p['is_ai'] && !isset($room['chosen'][$p['player_id']])) $remain++;
            }
            $room['messages'][] = array(
                'type' => 'card_played',
                'player_id' => $pid,
                'card' => $card,
                'remaining' => $remain,
                'player_name' => $pname,
                'played_cards' => $room['chosen']
            );
            $allPlayed = true;
            foreach ($room['players'] as $p) {
                if (!$p['is_ai'] && !isset($room['chosen'][$p['player_id']])) { $allPlayed = false; break; }
            }
            if ($allPlayed) {
                foreach ($room['players'] as &$p) {
                    if ($p['is_ai'] && !isset($room['chosen'][$p['player_id']]) && !empty($p['hand'])) {
                        $strat = isset($p['strategy']) ? $p['strategy'] : 'greedy';
                        $ac = aiChooseCard($p['hand'], $room['rows'], $strat);
                        if ($ac !== null) {
                            $room['chosen'][$p['player_id']] = $ac;
                            $k = array_search($ac, $p['hand']);
                            if ($k !== false) array_splice($p['hand'], $k, 1);
                        }
                    }
                }
                unset($p);
                startRoundProcessing($room, $rid);
            }
            $nowsend = microtime(true);
            foreach ($room['messages'] as &$msend) {
                if (!isset($msend['_ts'])) $msend['_ts'] = $nowsend;
                if (!isset($msend['_delivered'])) $msend['_delivered'] = array();
            }
            unset($msend);
            ftruncate($fpsend, 0); fseek($fpsend, 0);
            fwrite($fpsend, json_encode($room));
            flock($fpsend, LOCK_UN);
            fclose($fpsend);
            echo json_encode(array('ok'=>true));
        }
        elseif ($msgType === 'pick_row') {
            $ri = intval($input['row']);
            $card = intval($input['card']);
            $snapshot = isset($input['rows_snapshot']) ? $input['rows_snapshot'] : '';
            // pick_row 加独占锁
            $fsend = getDataFile($rid);
            $fpsend = fopen($fsend, 'c+');
            if (!$fpsend) { echo json_encode(array('error'=>'Room not found')); exit(); }
            flock($fpsend, LOCK_EX);
            $jsend = stream_get_contents($fpsend);
            $room = $jsend ? json_decode($jsend, true) : null;
            if (!$room) { flock($fpsend, LOCK_UN); fclose($fpsend); echo json_encode(array('error'=>'Room not found')); exit(); }
            handlePickRow($room, $rid, $pid, $ri, $card, $snapshot);
            $nowsend = microtime(true);
            foreach ($room['messages'] as &$msend) {
                if (!isset($msend['_ts'])) $msend['_ts'] = $nowsend;
                if (!isset($msend['_delivered'])) $msend['_delivered'] = array();
            }
            unset($msend);
            ftruncate($fpsend, 0); fseek($fpsend, 0);
            fwrite($fpsend, json_encode($room));
            flock($fpsend, LOCK_UN);
            fclose($fpsend);
            echo json_encode(array('ok'=>true));
        }
        else {
            echo json_encode(array('error'=>'Unknown message type'));
        }
        break;

    case 'poll':
        $rid = isset($_GET['room_id']) ? $_GET['room_id'] : '';
        $pid = isset($_GET['player_id']) ? $_GET['player_id'] : '';
        $since = floatval(isset($_GET['since']) ? $_GET['since'] : 0);

        // 检查房间是否存在
        $room = readRoom($rid);
        if (!$room) { echo json_encode(array('type'=>'error','detail'=>'Room not found')); exit(); }

        // 若房间在等待且人数满了，自动开始游戏（加锁保护）
        if ($room['phase'] === 'waiting') {
            $f = getDataFile($rid);
            $fp2 = fopen($f, 'c+');
            if ($fp2) {
                flock($fp2, LOCK_EX);
                $json2 = stream_get_contents($fp2);
                $room2 = $json2 ? json_decode($json2, true) : null;
                if ($room2 && $room2['phase'] === 'waiting') {
                    $rc = 0;
                    foreach ($room2['players'] as $p) if (!$p['is_ai']) $rc++;
                    if ($rc >= $room2['num_humans']) {
                        startGameForRoom($room2);
                        $now2 = microtime(true);
                        foreach ($room2['messages'] as &$m2) {
                            if (!isset($m2['_ts'])) $m2['_ts'] = $now2;
                            if (!isset($m2['_delivered'])) $m2['_delivered'] = array();
                        }
                        unset($m2);
                        ftruncate($fp2, 0); fseek($fp2, 0);
                        fwrite($fp2, json_encode($room2));
                    }
                }
                flock($fp2, LOCK_UN);
                fclose($fp2);
            }
        }

        $start = microtime(true);
        $msgs = array();
        while ((microtime(true) - $start) < $POLL_TIMEOUT) {
            // 用原子锁操作获取消息，不覆盖 room 核心状态
            list($latestRoom, $newMsgs) = atomicDeliverMessages($rid, $pid, $since);
            if ($latestRoom === null) break;

            // 若房间仍在等待且人数满了（循环中再次判断）
            if ($latestRoom['phase'] === 'waiting') {
                $rc = 0;
                foreach ($latestRoom['players'] as $p) if (!$p['is_ai']) $rc++;
                if ($rc >= $latestRoom['num_humans']) {
                    $f3 = getDataFile($rid);
                    $fp3 = fopen($f3, 'c+');
                    if ($fp3) {
                        flock($fp3, LOCK_EX);
                        $j3 = stream_get_contents($fp3);
                        $r3 = $j3 ? json_decode($j3, true) : null;
                        if ($r3 && $r3['phase'] === 'waiting') {
                            $rc3 = 0;
                            foreach ($r3['players'] as $p) if (!$p['is_ai']) $rc3++;
                            if ($rc3 >= $r3['num_humans']) {
                                startGameForRoom($r3);
                                $nowx = microtime(true);
                                foreach ($r3['messages'] as &$mx) {
                                    if (!isset($mx['_ts'])) $mx['_ts'] = $nowx;
                                    if (!isset($mx['_delivered'])) $mx['_delivered'] = array();
                                }
                                unset($mx);
                                ftruncate($fp3, 0); fseek($fp3, 0);
                                fwrite($fp3, json_encode($r3));
                            }
                        }
                        flock($fp3, LOCK_UN);
                        fclose($fp3);
                    }
                    // 重新获取消息
                    list($latestRoom, $newMsgs) = atomicDeliverMessages($rid, $pid, $since);
                    if ($latestRoom === null) break;
                }
            }

            if (!empty($newMsgs)) {
                $msgs = $newMsgs;
                break;
            }
            usleep(500000);
        }
        echo json_encode(array('messages' => $msgs, 'ts' => microtime(true)));
        break;

    case 'room_info':
        $rid = isset($_GET['room_id']) ? $_GET['room_id'] : '';
        $room = readRoom($rid);
        if (!$room) { echo json_encode(array('error'=>'Room not found')); exit(); }
        $sd = array();
        foreach ($room['players'] as $p) $sd[$p['player_id']] = $p['score'];
        echo json_encode(array(
            'phase' => $room['phase'],
            'round' => $room['round'],
            'scores' => $sd,
            'players' => playerList($room),
            'logs' => array_slice($room['logs'], 0, 20),
            'num_humans' => isset($room['num_humans']) ? $room['num_humans'] : 0,
            'num_ai' => isset($room['num_ai']) ? $room['num_ai'] : 0
        ));
        break;

    // ============================================================
    // Admin actions (for admin.html)
    // ============================================================
    case 'admin_list':
        cleanupStaleRooms();
        $files = glob($DATA_DIR . 'room_*.json');
        $rooms = array();
        $now = microtime(true);
        foreach ($files as $f) {
            $r = json_decode(file_get_contents($f), true);
            if (!$r) continue;
            $actualHumans = 0;
            $actualAI = 0;
            $hostName = '';
            foreach ($r['players'] as $p) {
                if ($p['is_ai']) $actualAI++;
                else { $actualHumans++; }
                if (isset($r['host_id']) && $p['player_id'] == $r['host_id']) {
                    $hostName = $p['name'];
                }
            }
            $rooms[] = array(
                'room_id'     => $r['room_id'],
                'phase'       => isset($r['phase']) ? $r['phase'] : 'unknown',
                'num_humans'  => isset($r['num_humans']) ? $r['num_humans'] : 0,
                'num_ai'      => isset($r['num_ai']) ? $r['num_ai'] : 0,
                'actual_humans' => $actualHumans,
                'actual_ai'   => $actualAI,
                'host_name'   => $hostName,
                'last_update' => isset($r['last_update']) ? $r['last_update'] : filemtime($f),
                'age_sec'     => intval($now - (isset($r['last_update']) ? $r['last_update'] : filemtime($f)))
            );
        }
        // Sort by last_update descending (newest first)
        usort($rooms, function($a, $b) { return $b['last_update'] - $a['last_update']; });
        echo json_encode(array(
            'total' => count($rooms),
            'rooms' => $rooms
        ));
        break;

    case 'admin_cleanup':
        $removed = cleanupStaleRooms();
        // Also force-remove rooms that are finished and older than 5 minutes
        $files = glob($DATA_DIR . 'room_*.json');
        $now = microtime(true);
        foreach ($files as $f) {
            $r = json_decode(file_get_contents($f), true);
            if ($r && isset($r['phase']) && $r['phase'] === 'finished') {
                if (filemtime($f) < ($now - 300)) {
                    @unlink($f);
                    $removed++;
                }
            }
        }
        echo json_encode(array('ok' => true, 'removed' => $removed));
        break;

    default:
        echo json_encode(array('error'=>'Unknown action'));
}
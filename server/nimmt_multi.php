<?php
/**
 * Nimmt! Multi-user Server - Fixed version (Async row pick fixed)
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
    return file_exists($f) ? json_decode(file_get_contents($f), true) : null;
}

function writeRoom($rid, $d) {
    $now = microtime(true);
    foreach ($d['messages'] as &$m) {
        if (!isset($m['_ts'])) $m['_ts'] = $now;
    }
    unset($m);
    file_put_contents(getDataFile($rid), json_encode($d));
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
    $cards = [];
    foreach ($room['chosen'] as $pid => $card) {
        $cards[] = ['pid' => $pid, 'card' => $card];
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
    if (!$player) {
        $room['round_queue_index']++;
        processNextCard($room, $rid);
        return;
    }

    $bestRow = findBestRow($room['rows'], $card);
    if ($bestRow === -1) {
        // 必须拿列
        if (!$player['is_ai']) {
            $room['pending_pick'] = [
                'player_id' => $pid,
                'card' => $card,
                'queue_index' => $idx,
                'rows_snapshot' => $room['rows']
            ];
            $room['messages'][] = [
                'type' => 'must_pick_row',
                'target_pid' => $pid,
                'card' => $card,
                'rows' => $room['rows'],
                'rows_snapshot' => json_encode($room['rows']),
            ];
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
        $room['logs'][] = ['msg' => $player['name'] . ' 打出 ' . $card . ' → 第' . ($bestRow+1) . '列', 'cls' => ''];
        $room['round_queue_index']++;
        processNextCard($room, $rid);
        return;
    }
}

function applyTakeRow(&$room, &$player, $rowIndex, $card) {
    $penalty = rowBulls($room['rows'][$rowIndex]);
    $player['score'] += $penalty;
    $room['logs'][] = ['msg' => $player['name'] . ' 收走第' . ($rowIndex+1) . '列 (-' . $penalty . '🐂)', 'cls' => 'penalty'];
    $room['rows'][$rowIndex] = [$card];
}

function handlePickRow(&$room, $rid, $pid, $chosenRow, $card, $rowsSnapshotJson) {
    if (!isset($room['pending_pick']) || $room['pending_pick']['player_id'] != $pid) return false;
    $pending = $room['pending_pick'];
    if ($pending['card'] != $card) return false;

    $player = null;
    foreach ($room['players'] as &$p) {
        if ($p['player_id'] == $pid) { $player = &$p; break; }
    }
    if (!$player) return false;

    applyTakeRow($room, $player, $chosenRow, $card);
    $room['round_queue_index'] = $pending['queue_index'] + 1;
    unset($room['pending_pick']);

    $sd = [];
    foreach ($room['players'] as $p) $sd[$p['player_id']] = $p['score'];
    $room['messages'][] = ['type' => 'row_picked', 'player_id' => $pid, 'row' => $chosenRow, 'rows' => $room['rows'], 'scores' => $sd];

    processNextCard($room, $rid);
    return true;
}

function finishRound(&$room, $rid) {
    $room['round']++;
    $room['chosen'] = [];
    $room['round_queue'] = [];
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
        $rk = [];
        foreach ($room['players'] as $p) {
            $rk[] = ['name' => $p['name'], 'score' => $p['score'], 'isYou' => false];
        }
        $room['messages'][] = ['type' => 'game_over', 'ranking' => $rk, 'logs' => array_slice($room['logs'], 0, 20)];
    } else {
        $sd = [];
        foreach ($room['players'] as $p) $sd[$p['player_id']] = $p['score'];
        $room['messages'][] = ['type' => 'round_end', 'round' => $room['round'], 'rows' => $room['rows'], 'scores' => $sd, 'logs' => array_slice($room['logs'], 0, 20)];
        autoPlayAI($room);
    }
    $room['last_update'] = microtime(true);
}

function autoPlayAI(&$room) {
    foreach ($room['players'] as &$p) {
        if ($p['is_ai'] && !empty($p['hand'])) {
            $strategy = $p['strategy'] ?? 'greedy';
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
    $room['chosen'] = [];
    $room['phase'] = 'playing';
    $room['logs'] = [];
    $room['round_queue'] = [];
    $room['round_queue_index'] = 0;
    $room['round_processing'] = false;
    unset($room['pending_pick']);

    for ($i = 0; $i < $total; $i++) {
        $room['players'][$i]['hand'] = $d['hands'][$i];
        $room['players'][$i]['score'] = 0;
    }
    foreach ($room['players'] as $p) {
        if (!$p['is_ai']) {
            $room['messages'][] = [
                'type' => 'game_start',
                'target_pid' => $p['player_id'],
                'hand' => $p['hand'],
                'rows' => $d['rows'],
                'round' => 1,
                'players' => array_map(function($pp) {
                    return ['id' => $pp['player_id'], 'name' => $pp['name'], 'score' => $pp['score'], 'is_ai' => $pp['is_ai']];
                }, $room['players'])
            ];
        }
    }
    $room['messages'][] = ['type' => 'game_started', 'players' => playerList($room)];
    autoPlayAI($room);
}

// ============================================================
// Main routing
// ============================================================
$input = json_decode(file_get_contents('php://input'), true);
if (!$input) $input = $_POST;
$action = $_REQUEST['action'] ?? ($input['action'] ?? '');

if (in_array($action, ['create_room','join_room','poll']) && mt_rand(1,100)<=5) cleanupStaleRooms();

switch ($action) {
    case 'create_room':
        $pid = $input['player_id'] ?? '';
        $pname = $input['player_name'] ?? ('P'.mt_rand(1,999));
        $numHumans = intval($input['num_humans'] ?? 2);
        $numAI = intval($input['num_ai'] ?? 0);
        if (!$pid) { echo json_encode(['error'=>'player_id required']); exit(); }
        $total = $numHumans + $numAI;
        if ($total < 2 || $total > 6) { echo json_encode(['error'=>'Total players must be 2~6']); exit(); }
        if ($numHumans < 1) { echo json_encode(['error'=>'Need at least 1 human player']); exit(); }
        $rid = sprintf('%06d', mt_rand(0, 999999));
        $aiNames = ['Robot Alpha', 'Robot Beta', 'Robot Gamma', 'Robot Delta', 'Robot Epsilon'];
        $aiStrategies = ['greedy', 'safe', 'random', 'greedy', 'safe'];
        $players = [['player_id' => $pid, 'name' => $pname, 'is_ai' => false, 'score' => 0, 'hand' => []]];
        for ($i = 0; $i < $numAI; $i++) {
            $players[] = [
                'player_id' => 'ai_' . $rid . '_' . $i,
                'name' => $aiNames[$i % count($aiNames)],
                'is_ai' => true,
                'score' => 0,
                'hand' => [],
                'strategy' => $aiStrategies[$i % count($aiStrategies)]
            ];
        }
        $room = [
            'room_id' => $rid,
            'name' => 'Room ' . substr($rid, 0, 6),
            'num_humans' => $numHumans,
            'num_ai' => $numAI,
            'max_players' => $total,
            'host_id' => $pid,
            'players' => $players,
            'phase' => 'waiting',
            'rows' => [],
            'round' => 1,
            'chosen' => [],
            'logs' => [],
            'messages' => [],
            'last_update' => microtime(true)
        ];
        writeRoom($rid, $room);
        echo json_encode([
            'room_id' => $rid,
            'name' => $room['name'],
            'players' => playerList($room),
            'num_humans' => $numHumans,
            'num_ai' => $numAI,
            'max_players' => $room['max_players']
        ]);
        break;

    case 'join_room':
        $rid = $input['room_id'] ?? '';
        $pid = $input['player_id'] ?? '';
        $pname = $input['player_name'] ?? ('P'.mt_rand(1,999));
        if (!$rid || !$pid) { echo json_encode(['error'=>'room_id and player_id required']); exit(); }
        $room = readRoom($rid);
        if (!$room) { echo json_encode(['error'=>'Room not found']); exit(); }
        $realCount = 0;
        foreach ($room['players'] as $p) if (!$p['is_ai']) $realCount++;
        if ($realCount >= $room['num_humans']) {
            $exists = false;
            foreach ($room['players'] as $p) if ($p['player_id'] == $pid) $exists = true;
            if (!$exists) { echo json_encode(['error'=>'Room is full (no more human slots)']); exit(); }
        }
        $exists = false;
        foreach ($room['players'] as $p) if ($p['player_id'] == $pid) $exists = true;
        if (!$exists) {
            $room['players'][] = ['player_id' => $pid, 'name' => $pname, 'is_ai' => false, 'score' => 0, 'hand' => []];
            $realCount++;
        }
        $room['messages'][] = [
            'type' => 'player_joined',
            'player_id' => $pid,
            'name' => $pname,
            'players' => playerList($room),
            'num_humans' => $room['num_humans'],
            'num_ai' => $room['num_ai']
        ];
        $room['last_update'] = microtime(true);
        writeRoom($rid, $room);
        echo json_encode([
            'room_id' => $rid,
            'name' => $room['name'],
            'players' => playerList($room),
            'num_humans' => $room['num_humans'],
            'num_ai' => $room['num_ai'],
            'max_players' => $room['max_players'],
            'phase' => $room['phase']
        ]);
        break;

    case 'send':
        $msgType = $input['type'] ?? '';
        $rid = $input['room_id'] ?? '';
        $pid = $input['player_id'] ?? '';
        $room = readRoom($rid);
        if (!$room) { echo json_encode(['error'=>'Room not found']); exit(); }

        if ($msgType === 'start_game') {
            if ($room['host_id'] !== $pid) { echo json_encode(['error'=>'Only host can start']); exit(); }
            if ($room['phase'] !== 'waiting') { echo json_encode(['error'=>'Game already started']); exit(); }
            startGameForRoom($room);
            writeRoom($rid, $room);
            echo json_encode(['ok'=>true]);
        }
        elseif ($msgType === 'play_card') {
            $card = intval($input['card']);
            if ($room['phase'] !== 'playing' || isset($room['chosen'][$pid])) {
                echo json_encode(['ok'=>true]); exit();
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
            $room['messages'][] = [
                'type' => 'card_played',
                'player_id' => $pid,
                'card' => $card,
                'remaining' => $remain,
                'player_name' => $pname,
                'played_cards' => $room['chosen']
            ];
            $allPlayed = true;
            foreach ($room['players'] as $p) {
                if (!$p['is_ai'] && !isset($room['chosen'][$p['player_id']])) { $allPlayed = false; break; }
            }
            if ($allPlayed) {
                foreach ($room['players'] as &$p) {
                    if ($p['is_ai'] && !isset($room['chosen'][$p['player_id']]) && !empty($p['hand'])) {
                        $ac = aiChooseCard($p['hand'], $room['rows'], $p['strategy']??'greedy');
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
            writeRoom($rid, $room);
            echo json_encode(['ok'=>true]);
        }
        elseif ($msgType === 'pick_row') {
            $ri = intval($input['row']);
            $card = intval($input['card']);
            $snapshot = $input['rows_snapshot'] ?? '';
            handlePickRow($room, $rid, $pid, $ri, $card, $snapshot);
            writeRoom($rid, $room);
            echo json_encode(['ok'=>true]);
        }
        else {
            echo json_encode(['error'=>'Unknown message type']);
        }
        break;

    case 'poll':
        $rid = $_GET['room_id'] ?? '';
        $pid = $_GET['player_id'] ?? '';
        $since = floatval($_GET['since'] ?? 0);
        $room = readRoom($rid);
        if (!$room) { echo json_encode(['type'=>'error','detail'=>'Room not found']); exit(); }
        if ($room['phase'] === 'waiting') {
            $realCount = 0;
            foreach ($room['players'] as $p) if (!$p['is_ai']) $realCount++;
            if ($realCount >= $room['num_humans']) {
                startGameForRoom($room);
                writeRoom($rid, $room);
                $room = readRoom($rid);
            }
        }
        $start = microtime(true);
        $msgs = [];
        while ((microtime(true) - $start) < $POLL_TIMEOUT) {
            $room = readRoom($rid);
            if (!$room) break;
            if ($room['phase'] === 'waiting') {
                $rc = 0;
                foreach ($room['players'] as $p) if (!$p['is_ai']) $rc++;
                if ($rc >= $room['num_humans']) {
                    startGameForRoom($room);
                    writeRoom($rid, $room);
                    $room = readRoom($rid);
                }
            }
            $newMsgs = [];
            $consumedKeys = [];
            foreach ($room['messages'] as $mi => $m) {
                if (isset($m['_ts']) && $m['_ts'] > $since) {
                    $targetOk = !isset($m['target_pid']) || $m['target_pid'] == $pid;
                    if ($targetOk) {
                        $newMsgs[] = $m;
                        $consumedKeys[] = $mi;
                    }
                }
            }
            if (!empty($consumedKeys)) {
                foreach ($consumedKeys as $ck) unset($room['messages'][$ck]);
                $room['messages'] = array_values($room['messages']);
                writeRoom($rid, $room);
            }
            if (!empty($newMsgs)) {
                $msgs = $newMsgs;
                break;
            }
            usleep(500000);
        }
        echo json_encode(['messages' => $msgs, 'ts' => microtime(true)]);
        break;

    case 'room_info':
        $rid = $_GET['room_id'] ?? '';
        $room = readRoom($rid);
        if (!$room) { echo json_encode(['error'=>'Room not found']); exit(); }
        $sd = [];
        foreach ($room['players'] as $p) $sd[$p['player_id']] = $p['score'];
        echo json_encode([
            'phase' => $room['phase'],
            'round' => $room['round'],
            'scores' => $sd,
            'logs' => array_slice($room['logs'], 0, 20),
            'num_humans' => $room['num_humans'] ?? 0,
            'num_ai' => $room['num_ai'] ?? 0
        ]);
        break;

    default:
        echo json_encode(['error'=>'Unknown action']);
}
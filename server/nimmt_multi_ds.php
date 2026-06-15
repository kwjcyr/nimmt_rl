<?php
/**
 * Nimmt! Multi-user Server - Fixed version (Async row pick fixed)
 *
 * Changes:
 * - Round processing is now step-by-step with pending_pick state for humans
 * - Cards are processed in sorted order, waiting for human row picks
 * - Fixed variable undefined bugs
 * - AI hands not exposed to humans
 *
 * MAX_ROW can be set to 5 or 6 (original code used 6)
 */
header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type');

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') exit(0);

$DATA_DIR = '/opt/nimmt/data/';
$POLL_TIMEOUT = 25;
$ROOM_TTL = 1800; // 30 minutes
@mkdir($DATA_DIR, 0777, true);

define('MAX_ROW', 6);          // You can change to 5 if you prefer standard rule
define('END_SCORE', 66);

// ============================================================
// Helper functions
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
function readRoom($rid) { $f = getDataFile($rid); return file_exists($f) ? json_decode(file_get_contents($f), true) : null; }
function writeRoom($rid, $d) {
    $now = microtime(true);
    foreach ($d['messages'] as &$m) {
        if (!isset($m['_ts'])) $m['_ts'] = $now;
    }
    unset($m);
    file_put_contents(getDataFile($rid), json_encode($d));
}

function getBulls($c) {
    if ($c == 55) return 7;
    if ($c % 11 == 0) return 5;
    if ($c % 10 == 0) return 3;
    if ($c % 5 == 0) return 2;
    return 1;
}
function rowBulls($row) { $s = 0; foreach ($row as $c) $s += getBulls($c); return $s; }

function findBestRow($rows, $card) {
    $best = -1; $md = 1e9;
    foreach ($rows as $r => $row) {
        $t = $row[count($row)-1];
        if ($t < $card && ($card - $t) < $md) { $md = $card - $t; $best = $r; }
    }
    return $best;
}

function aiChooseCard($hand, $rows, $strategy) {
    if (empty($hand)) return null;
    if ($strategy === 'random') return $hand[array_rand($hand)];
    $scored = array();
    foreach ($hand as $card) {
        $br = findBestRow($rows, $card);
        if ($br === -1) { $min = PHP_INT_MAX; foreach ($rows as $r) $min = min($min, rowBulls($r)); $risk = $min + 80; }
        elseif (count($rows[$br]) >= MAX_ROW) $risk = rowBulls($rows[$br]) + 40;
        else $risk = count($rows[$br]) * 3;
        $scored[] = array('card' => $card, 'risk' => $risk + (mt_rand() / mt_getrandmax() - 0.5) * 10);
    }
    usort($scored, function($a,$b){ return $a['risk'] - $b['risk']; });
    if ($strategy === 'safe') return $scored[0]['card'];
    $r = mt_rand() / mt_getrandmax();
    if ($r < 0.70) { $p = array_slice($scored, 0, max(1, count($scored)/3)); return $p[array_rand($p)]['card']; }
    elseif ($r < 0.90) { $m = floor(count($scored)/2); $p = array_slice($scored, max(0,$m-1), $m+2); $p = array_values(array_filter($p)); return $p[array_rand($p)]['card']; }
    return $hand[count($hand)-1];
}

function aiChooseRow($rows) {
    $b = PHP_INT_MAX; $bi = 0;
    foreach ($rows as $i => $r) {
        $rb = rowBulls($r);
        if ($rb < $b) { $b = $rb; $bi = $i; }
    }
    return $bi;
}

function deal($numPlayers) {
    $deck = range(1, 100); shuffle($deck);
    $hands = array();
    for ($i = 0; $i < $numPlayers; $i++) $hands[] = array_slice($deck, $i*10, 10);
    foreach ($hands as &$h) sort($h);
    $baseCards = array_slice($deck, $numPlayers*10, 5); sort($baseCards);
    $rows = array(); foreach ($baseCards as $c) $rows[] = array($c);
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
    // Build sorted queue of (pid, card) from $room['chosen']
    $cards = array();
    foreach ($room['chosen'] as $pid => $card) {
        $cards[] = array('pid' => $pid, 'card' => $card);
    }
    usort($cards, function($a, $b) { return $a['card'] - $b['card']; });

    $room['round_queue'] = $cards;
    $room['round_queue_index'] = 0;
    $room['round_processing'] = true;
    // No need for snapshot here; we'll process sequentially

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

    // Find player
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
        // Card smaller than all row ends -> must pick a row
        if (!$player['is_ai']) {
            // Human: wait for pick_row
            $room['pending_pick'] = array(
                'player_id' => $pid,
                'card' => $card,
                'queue_index' => $idx,
                'rows_snapshot' => $room['rows']   // for validation
            );
            $room['messages'][] = array(
                'type' => 'must_pick_row',
                'target_pid' => $pid,
                'card' => $card,
                'rows' => $room['rows'],
                'rows_snapshot' => json_encode($room['rows']),
            );
            // Do NOT advance index; wait for pick_row
            return;
        } else {
            $chosenRow = aiChooseRow($room['rows']);
            applyTakeRow($room, $player, $chosenRow, $card);
            $room['round_queue_index']++;
            processNextCard($room, $rid);
            return;
        }
    } elseif (count($room['rows'][$bestRow]) >= MAX_ROW) {
        $chosenRow = $bestRow;
        applyTakeRow($room, $player, $chosenRow, $card);
        $room['round_queue_index']++;
        processNextCard($room, $rid);
        return;
    } else {
        // Normal placement
        $room['rows'][$bestRow][] = $card;
        $room['logs'][] = array('msg' => $player['name'] . ' played ' . $card . ' -> row ' . ($bestRow+1), 'cls' => '');
        $room['round_queue_index']++;
        processNextCard($room, $rid);
        return;
    }
}

function applyTakeRow(&$room, &$player, $rowIndex, $card) {
    $penalty = rowBulls($room['rows'][$rowIndex]);
    $player['score'] += $penalty;
    $room['logs'][] = array('msg' => $player['name'] . ' took row ' . ($rowIndex+1) . ' (-' . $penalty . ')', 'cls' => 'penalty');
    $room['rows'][$rowIndex] = array($card);
}

function handlePickRow(&$room, $rid, $pid, $chosenRow, $card, $rowsSnapshotJson) {
    if (!isset($room['pending_pick']) || $room['pending_pick']['player_id'] != $pid) {
        return false;
    }
    $pending = $room['pending_pick'];
    if ($pending['card'] != $card) {
        return false;
    }
    // Optional validation: could check snapshot, but we trust client or do simple check
    // Apply the take using current rows
    $player = null;
    foreach ($room['players'] as &$p) {
        if ($p['player_id'] == $pid) { $player = &$p; break; }
    }
    if (!$player) return false;

    applyTakeRow($room, $player, $chosenRow, $card);

    // Advance index to after this pending card
    $room['round_queue_index'] = $pending['queue_index'] + 1;
    unset($room['pending_pick']);

    // Send row_picked message
    $sd = array();
    foreach ($room['players'] as $p) $sd[$p['player_id']] = $p['score'];
    $room['messages'][] = array('type' => 'row_picked', 'player_id' => $pid, 'row' => $chosenRow, 'rows' => $room['rows'], 'scores' => $sd);

    // Continue processing next card
    processNextCard($room, $rid);
    return true;
}

function finishRound(&$room, $rid) {
    $room['round']++;
    $room['chosen'] = array();
    $room['round_queue'] = array();
    $room['round_queue_index'] = 0;
    $room['round_processing'] = false;
    unset($room['pending_pick']);

    // Check end conditions
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
            $rk[] = array('name' => $p['name'], 'score' => $p['score'], 'isYou' => false);
        }
        $room['messages'][] = array('type' => 'game_over', 'ranking' => $rk, 'logs' => array_slice($room['logs'], 0, 20));
    } else {
        $sd = array();
        foreach ($room['players'] as $p) $sd[$p['player_id']] = $p['score'];
        $room['messages'][] = array('type' => 'round_end', 'round' => $room['round'], 'rows' => $room['rows'], 'scores' => $sd, 'logs' => array_slice($room['logs'], 0, 20));
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
    $totalPlayers = count($room['players']);
    $d = deal($totalPlayers);
    $room['rows'] = $d['rows'];
    $room['round'] = 1;
    $room['chosen'] = array();
    $room['phase'] = 'playing';
    $room['logs'] = array();
    $room['round_queue'] = array();
    $room['round_queue_index'] = 0;
    $room['round_processing'] = false;
    unset($room['pending_pick']);

    for ($i = 0; $i < $totalPlayers; $i++) {
        $room['players'][$i]['hand'] = $d['hands'][$i];
        $room['players'][$i]['score'] = 0;
    }

    // Send game_start only to humans (AI don't need hand data)
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

    $room['messages'][] = array(
        'type' => 'game_started',
        'players' => playerList($room)
    );

    autoPlayAI($room);
}

// ============================================================
// Main Routing
// ============================================================

$input = json_decode(file_get_contents('php://input'), true);
if (!$input) $input = $_POST;
$action = isset($_REQUEST['action']) ? $_REQUEST['action'] : '';
if (!$action && isset($input['action'])) $action = $input['action'];

if (in_array($action, array('create_room', 'join_room', 'poll'))) {
    if (mt_rand(1, 100) <= 5) cleanupStaleRooms();
}

switch ($action) {

case 'create_room':
    $pid = isset($input['player_id']) ? $input['player_id'] : '';
    $pname = isset($input['player_name']) ? $input['player_name'] : ('P'.mt_rand(1,999));
    $numHumans = isset($input['num_humans']) ? intval($input['num_humans']) : 2;
    $numAI = isset($input['num_ai']) ? intval($input['num_ai']) : 0;

    if (!$pid) { echo json_encode(array('error' => 'player_id required')); exit(); }

    $total = $numHumans + $numAI;
    if ($total < 2 || $total > 6) {
        echo json_encode(array('error' => 'Total players must be 2~6')); exit();
    }
    if ($numHumans < 1) {
        echo json_encode(array('error' => 'Need at least 1 human player')); exit();
    }

    $rid = sprintf('%06d', mt_rand(0, 999999));

    $aiNames = array('Robot Alpha', 'Robot Beta', 'Robot Gamma', 'Robot Delta', 'Robot Epsilon');
    $aiStrategies = array('greedy', 'safe', 'random', 'greedy', 'safe');

    $players = array();
    $players[] = array(
        'player_id' => $pid,
        'name' => $pname,
        'is_ai' => false,
        'score' => 0,
        'hand' => array()
    );

    for ($i = 0; $i < $numAI; $i++) {
        $aiId = 'ai_' . $rid . '_' . $i;
        $players[] = array(
            'player_id' => $aiId,
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
    if (!$rid || !$pid) { echo json_encode(array('error' => 'room_id and player_id required')); exit(); }

    $room = readRoom($rid);
    if (!$room) { echo json_encode(array('error' => 'Room not found')); exit(); }

    $realCount = 0;
    foreach ($room['players'] as $p) { if (!$p['is_ai']) $realCount++; }

    if ($realCount >= $room['num_humans']) {
        $exists = false;
        foreach ($room['players'] as $p) { if ($p['player_id'] == $pid) $exists = true; }
        if (!$exists) {
            echo json_encode(array('error' => 'Room is full (no more human slots)')); exit();
        }
    }

    $exists = false;
    foreach ($room['players'] as $p) { if ($p['player_id'] == $pid) $exists = true; }
    if (!$exists) {
        $room['players'][] = array(
            'player_id' => $pid,
            'name' => $pname,
            'is_ai' => false,
            'score' => 0,
            'hand' => array()
        );
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
    writeRoom($rid, $room);

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

    $room = readRoom($rid);
    if (!$room) { echo json_encode(array('error' => 'Room not found')); exit(); }

    if ($msgType === 'start_game') {
        if ($room['host_id'] !== $pid) { echo json_encode(array('error' => 'Only host can start')); exit(); }
        if ($room['phase'] !== 'waiting') { echo json_encode(array('error' => 'Game already started')); exit(); }

        startGameForRoom($room);
        $room['last_update'] = microtime(true);
        writeRoom($rid, $room);
        echo json_encode(array('ok' => true));
    }
    elseif ($msgType === 'play_card') {
        $card = intval($input['card']);
        if ($room['phase'] !== 'playing' || isset($room['chosen'][$pid])) {
            echo json_encode(array('ok' => true)); exit();
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

        // Sort played_cards by card value ascending so frontend shows correct order
        $playedCards = $room['chosen'];
        uksort($playedCards, function($a,$b) use($playedCards){ return $playedCards[$a] - $playedCards[$b]; });

        $room['messages'][] = array(
            'type' => 'card_played',
            'player_id' => $pid,
            'card' => $card,
            'remaining' => $remain,
            'player_name' => $pname,
            'played_cards' => $playedCards
        );

        // Check if all human players have played
        $allPlayed = true;
        foreach ($room['players'] as $p) {
            if (!$p['is_ai'] && !isset($room['chosen'][$p['player_id']])) {
                $allPlayed = false; break;
            }
        }

        if ($allPlayed) {
            // Ensure AI have played (autoPlayAI should have done, but double-check)
            foreach ($room['players'] as &$p) {
                if ($p['is_ai'] && !isset($room['chosen'][$p['player_id']]) && !empty($p['hand'])) {
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
            startRoundProcessing($room, $rid);
        }

        $room['last_update'] = microtime(true);
        writeRoom($rid, $room);
        echo json_encode(array('ok' => true));
    }
    elseif ($msgType === 'pick_row') {
        $ri = intval($input['row']);
        $card = intval($input['card']);
        $rowsSnapshot = isset($input['rows_snapshot']) ? $input['rows_snapshot'] : '';
        $success = handlePickRow($room, $rid, $pid, $ri, $card, $rowsSnapshot);
        $room['last_update'] = microtime(true);
        writeRoom($rid, $room);
        echo json_encode(array('ok' => true, 'success' => $success));
    }
    else {
        echo json_encode(array('error' => 'Unknown message type'));
    }
    break;

case 'poll':
    $rid = isset($_GET['room_id']) ? $_GET['room_id'] : '';
    $pid = isset($_GET['player_id']) ? $_GET['player_id'] : '';
    $since = isset($_GET['since']) ? floatval($_GET['since']) : 0;

    $room = readRoom($rid);
    if (!$room) { echo json_encode(array('type' => 'error', 'detail' => 'Room not found')); exit(); }

    if ($room['phase'] === 'waiting') {
        $realCount = 0;
        foreach ($room['players'] as $p) { if (!$p['is_ai']) $realCount++; }
        if ($realCount >= $room['num_humans']) {
            startGameForRoom($room);
            $room['last_update'] = microtime(true);
            writeRoom($rid, $room);
            $room = readRoom($rid);
        }
    }

    $start = microtime(true);
    $msgs = array();

    while ((microtime(true) - $start) < $POLL_TIMEOUT) {
        $room = readRoom($rid);
        if (!$room) break;

        if ($room['phase'] === 'waiting') {
            $rc = 0;
            foreach ($room['players'] as $p) { if (!$p['is_ai']) $rc++; }
            if ($rc >= $room['num_humans']) {
                startGameForRoom($room);
                $room['last_update'] = microtime(true);
                writeRoom($rid, $room);
                $room = readRoom($rid);
            }
        }

        $newMsgs = array();
        $consumedKeys = array();
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
            foreach ($consumedKeys as $ck) {
                unset($room['messages'][$ck]);
            }
            $room['messages'] = array_values($room['messages']);
            writeRoom($rid, $room);
        }

        if (!empty($newMsgs)) { $msgs = $newMsgs; break; }
        usleep(500000);
    }

    echo json_encode(array('messages' => $msgs, 'ts' => microtime(true)));
    break;

case 'room_info':
    $rid = isset($_GET['room_id']) ? $_GET['room_id'] : '';
    $room = readRoom($rid);
    if (!$room) { echo json_encode(array('error' => 'Room not found')); exit(); }
    $sd = array();
    foreach ($room['players'] as $p) $sd[$p['player_id']] = $p['score'];
    echo json_encode(array(
        'phase' => $room['phase'],
        'round' => $room['round'],
        'scores' => $sd,
        'logs' => array_slice($room['logs'], 0, 20),
        'num_humans' => isset($room['num_humans']) ? $room['num_humans'] : 0,
        'num_ai' => isset($room['num_ai']) ? $room['num_ai'] : 0
    ));
    break;

case 'admin_list':
    $files = glob($DATA_DIR . 'room_*.json');
    $rooms = array();
    foreach ($files as $f) {
        $r = json_decode(file_get_contents($f), true);
        if (!$r) continue;
        $realCount = 0; $aiCount = 0;
        if (isset($r['players'])) {
            foreach ($r['players'] as $p) {
                if (!empty($p['is_ai'])) $aiCount++; else $realCount++;
            }
        }
        $rooms[] = array(
            'room_id' => isset($r['room_id']) ? $r['room_id'] : '?',
            'phase' => isset($r['phase']) ? $r['phase'] : 'unknown',
            'num_humans' => isset($r['num_humans']) ? $r['num_humans'] : $realCount,
            'num_ai' => isset($r['num_ai']) ? $r['num_ai'] : $aiCount,
            'actual_humans' => $realCount,
            'actual_ai' => $aiCount,
            'total_players' => count($r['players']),
            'round' => isset($r['round']) ? $r['round'] : 0,
            'host_name' => isset($r['players'][0]['name']) ? $r['players'][0]['name'] : '',
            'created' => filemtime($f),
            'age_sec' => time() - filemtime($f)
        );
    }
    usort($rooms, function($a,$b){ return $b['created'] - $a['created']; });
    echo json_encode(array('total' => count($rooms), 'rooms' => $rooms, 'server_time' => time()));
    break;

case 'admin_cleanup':
    $removed = cleanupStaleRooms();
    echo json_encode(array('ok' => true, 'removed' => $removed));
    break;

default:
    echo json_encode(array('error' => 'Unknown action: create_room, join_room, send, poll, room_info, admin_list, admin_cleanup'));
}
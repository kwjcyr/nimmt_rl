<?php
/**
 * Nimmt! Multi-user Server - PHP 5.3 Compatible
 * Supports: num_humans (actual players needed) + num_ai (AI players)
 * Auto-starts game when all humans have joined
 *
 */
header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type');

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') exit(0);

$DATA_DIR = '/opt/nimmt/data/';
$POLL_TIMEOUT = 25;
$ROOM_TTL = 1800; // 30 minutes - delete rooms inactive for this long
@mkdir($DATA_DIR, 0777, true);

define('MAX_ROW', 6);
define('END_SCORE', 66);

// ============================================================
// Room cleanup (remove stale rooms)
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

// Auto-cleanup on some requests (not all to avoid overhead)
if (in_array($action, array('create_room', 'join_room', 'poll'))) {
    // Cleanup roughly every 5 minutes (random chance)
    if (mt_rand(1, 100) <= 5) cleanupStaleRooms();
}

function getDataFile($rid) {
    global $DATA_DIR;
    return $DATA_DIR . 'room_' . preg_replace('/[^a-zA-Z0-9]/', '', $rid) . '.json';
}
function readRoom($rid) { $f = getDataFile($rid); return file_exists($f) ? json_decode(file_get_contents($f), true) : null; }
function writeRoom($rid, $d) {
    // Auto-add _ts to any messages missing it
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

function resolveRound(&$room, $rid) {
    // Pass 1: Process all cards in sorted order. When a human must_pick_row is needed,
    uksort($chosen, function($a,$b) use($chosen){ return $chosen[$a] - $chosen[$b]; });

    foreach ($chosen as $pi => $card) {
        $br = findBestRow($room['rows'], $card);
        $po = null;
        foreach ($room['players'] as $k => $p) {
            if ($p['player_id'] === $pi) { $po = &$room['players'][$k]; break; }
        }

        if ($br === -1) {
            // Card is smaller than all row ends - must pick a row
            if ($po && !$po['is_ai']) {
                // Human player: send must_pick_row message and wait
                // Include rows_snapshot so we use consistent row data even under concurrent access
                $room['messages'][] = array(
                    'type' => 'must_pick_row',
                    'target_pid' => $pi,
                    'card' => $card,
                    'rows' => $room['rows'],
                    'rows_snapshot' => json_encode($room['rows']),  // snapshot for concurrency safety
                );
                unset($po);
                continue;
            } else {
                // AI player: auto choose row
                $r = aiChooseRow($room['rows']);
            }
        } elseif (count($room['rows'][$br]) >= MAX_ROW) {
            // Row is full, take it
            $r = $br;
        } else {
            // Normal placement
            $room['rows'][$br][] = $card;
            if ($po) {
                $room['logs'][] = array('msg' => $po['name'] . ' played ' . $card . ' -> row ' . ($br+1), 'cls' => '');
            }
            unset($po);
            continue;
        }

        // Take row (penalty)
        $penalty = rowBulls($room['rows'][$r]);
        $room['rows'][$r] = array($card);
        if ($po) {
            $po['score'] += $penalty;
            $cls = $penalty > 0 ? 'penalty' : '';
            $room['logs'][] = array('msg' => $po['name'] . ' took row ' . ($r+1) . ' (-' . $penalty . ')', 'cls' => $cls);
        }
        unset($po);
    }

    $room['round']++;
    $room['chosen'] = array();

    // Check end conditions
    $handsEmpty = true;
    $someoneMax = false;
    foreach ($room['players'] as $p) {
        if (!$p['is_ai'] && !empty($p['hand'])) $handsEmpty = false;
        if ($p['score'] >= END_SCORE) $someoneMax = true;
    }
    // Also check AI hands
    foreach ($room['players'] as $p) {
        if ($p['is_ai'] && !empty($p['hand'])) $handsEmpty = false;
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

        // Auto-play AI cards for new round
        autoPlayAI($room);
    }

    $room['last_update'] = microtime(true);
}

function autoPlayAI(&$room) {
    // AI players auto-play their cards at the start of each round
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
    // Initialize round-resolution state

    for ($i = 0; $i < $totalPlayers; $i++) {
        $room['players'][$i]['hand'] = $d['hands'][$i];
        $room['players'][$i]['score'] = 0;
    }

    // Send game_start to each player
    foreach ($room['players'] as $idx => $p) {
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

    $room['messages'][] = array(
        'type' => 'game_started',
        'players' => playerList($room)
    );

    // Auto-play AI cards for first round
    autoPlayAI($room);
}

// ============================================================
// Main Routing
// ============================================================

$input = json_decode(file_get_contents('php://input'), true);
if (!$input) $input = $_POST;
// Try to get action from query string first, then from JSON body
$action = isset($_REQUEST['action']) ? $_REQUEST['action'] : '';
if (!$action && isset($input['action'])) $action = $input['action'];

switch ($action) {

case 'create_room':
    $pid = isset($input['player_id']) ? $input['player_id'] : '';
    $pname = isset($input['player_name']) ? $input['player_name'] : ('P'.mt_rand(1,999));
    $numHumans = isset($input['num_humans']) ? intval($input['num_humans']) : 2;
    $numAI = isset($input['num_ai']) ? intval($input['num_ai']) : 0;

    if (!$pid) { echo json_encode(array('error' => 'player_id required')); exit(); }

    // Validate
    $total = $numHumans + $numAI;
    if ($total < 2 || $total > 6) {
        echo json_encode(array('error' => 'Total players must be 2~6')); exit();
    }
    if ($numHumans < 1) {
        echo json_encode(array('error' => 'Need at least 1 human player')); exit();
    }

    $rid = sprintf('%06d', mt_rand(0, 999999));

    // AI names
    $aiNames = array('Robot Alpha', 'Robot Beta', 'Robot Gamma', 'Robot Delta', 'Robot Epsilon');
    $aiStrategies = array('greedy', 'safe', 'random', 'greedy', 'safe');

    $players = array();
    // Add the human host
    $players[] = array(
        'player_id' => $pid,
        'name' => $pname,
        'is_ai' => false,
        'score' => 0,
        'hand' => array()
    );

    // Add AI players
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

    // Count current real players
    $realCount = 0;
    foreach ($room['players'] as $p) { if (!$p['is_ai']) $realCount++; }

    // Check if already at capacity for real players
    if ($realCount >= $room['num_humans']) {
        $exists = false;
        foreach ($room['players'] as $p) { if ($p['player_id'] == $pid) $exists = true; }
        if (!$exists) {
            echo json_encode(array('error' => 'Room is full (no more human slots)')); exit();
        }
    }

    // Add player if not already in room
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

    // Notify all players
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
    $pname = '';

    $room = readRoom($rid);
    if (!$room) { echo json_encode(array('error' => 'Room not found')); exit(); }

    // Find player name
    foreach ($room['players'] as $p) {
        if ($p['player_id'] == $pid) { $pname = $p['name']; break; }
    }

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
        foreach ($room['players'] as &$p) {
            if ($p['player_id'] == $pid) {
                $k = array_search($card, $p['hand']);
                if ($k !== false) array_splice($p['hand'], $k, 1);
            }
        }
        unset($p);

        // Count remaining human players who haven't played
        $remain = 0;
        foreach ($room['players'] as $p) {
            if (!$p['is_ai'] && !isset($room['chosen'][$p['player_id']])) $remain++;
        }

        // Sort played_cards by card value ascending so frontend shows correct order
        $playedCards = $room['chosen'];
        uksort($playedCards, function($a,$b) use($playedCards){ return $playedCards[$a] - $playedCards[$b]; });

        $room['messages'][] = array('type' => 'card_played', 'player_id' => $pid, 'card' => $card, 'remaining' => $remain, 'player_name' => ($po ? $po['name'] : ''), 'played_cards' => $playedCards);

        // Check if all human players have played
        $allPlayed = true;
        foreach ($room['players'] as $p) {
            if (!$p['is_ai'] && !isset($room['chosen'][$p['player_id']])) {
                $allPlayed = false; break;
            }
        }

        if ($allPlayed) {
            // AI should already have played (autoPlayAI), but check for safety
            foreach ($room['players'] as &$p) {
                if ($p['is_ai'] && !isset($room['chosen'][$p['player_id']]) && !empty($p['hand'])) {
                    $strategy = isset($p['strategy']) ? $p['strategy'] : 'greedy';
                    $ac = aiChooseCard($p['hand'], $room['rows'], $strategy);
                    $room['chosen'][$p['player_id']] = $ac;
                    $k = array_search($ac, $p['hand']);
                    if ($k !== false) array_splice($p['hand'], $k, 1);
                }
            }
            unset($p);
            resolveRound($room, $rid);
        }

        $room['last_update'] = microtime(true);
        writeRoom($rid, $room);
        echo json_encode(array('ok' => true));
    }
    elseif ($msgType === 'pick_row') {
        $ri = intval($input['row']);
        $card = intval($input['card']);

        // Use rows_snapshot for concurrency safety (prevents two players corrupting same row)
        $rowsData = isset($input['rows_snapshot']) ? json_decode($input['rows_snapshot'], true) : null;
        if (!$rowsData) $rowsData = $room['rows'];

        // Validate the row index and card still matches (concurrency check)
        $expectedCard = isset($rowsData[$ri][0]) ? $rowsData[$ri][0] : null;
        if ($expectedCard !== null && $expectedCard != $card) {
            echo json_encode(array('ok' => true, 'warning' => 'row_mismatch', 'expected' => $expectedCard, 'got' => $card));
            exit();
        }

        // Apply penalty: human picks a row because their card was too small
        $penalty = rowBulls($rowsData[$ri]);

        foreach ($room['players'] as &$p) {
            if ($p['player_id'] == $pid) $p['score'] += $penalty;
        }
        unset($p);

        $room['rows'][$ri] = array($card);
        $rowsData[$ri] = array($card);
        $room['logs'][] = array('msg' => $pname . ' took row ' . ($ri+1) . ' (-' . $penalty . ')', 'cls' => 'penalty');

        $sd = array();
        foreach ($room['players'] as $p) $sd[$p['player_id']] = $p['score'];

        $room['messages'][] = array('type' => 'row_picked', 'player_id' => $pid, 'row' => $ri, 'rows' => $rowsData, 'scores' => $sd);

        // After row is picked, check if all pending picks are resolved for this round
        $pendingPicks = false;
        foreach ($room['messages'] as $m) {
            if (isset($m['type']) && $m['type'] === 'must_pick_row' && !isset($m['_resolved'])) {
                $pendingPicks = true; break;
            }
        }

        $room['last_update'] = microtime(true);
        writeRoom($rid, $room);
        echo json_encode(array('ok' => true));
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

    // Check if we should auto-start (all humans joined)
    if ($room['phase'] === 'waiting') {
        $realCount = 0;
        foreach ($room['players'] as $p) { if (!$p['is_ai']) $realCount++; }
        if ($realCount >= $room['num_humans']) {
            // Auto-start the game!
            startGameForRoom($room);
            $room['last_update'] = microtime(true);
            writeRoom($rid, $room);
            // Re-read room after auto-start
            $room = readRoom($rid);
        }
    }

    $start = microtime(true);
    $msgs = array();

    while ((microtime(true) - $start) < $POLL_TIMEOUT) {
        $room = readRoom($rid);
        if (!$room) break;

        // Check auto-start again in case someone joined while we were polling
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
                // Don't unset non-target messages - let them for other players
            }
        }

        // Only remove consumed messages and write back if we actually consumed something
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

// ============================================================
// Admin APIs
// ============================================================

case 'admin_list':
    // List all active rooms with summary info
    $files = glob($DATA_DIR . 'room_*.json');
    $rooms = array();
    foreach ($files as $f) {
        $r = json_decode(file_get_contents($f), true);
        if (!$r) continue;
        $realCount = 0;
        $aiCount = 0;
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
            'host_name' => '',
            'created' => filemtime($f),
            'age_sec' => time() - filemtime($f)
        );
        // Get host name
        if (isset($r['players']) && !empty($r['players'])) {
            $rooms[count($rooms)-1]['host_name'] = $r['players'][0]['name'];
        }
    }
    usort($rooms, function($a,$b){ return $b['created'] - $a['created']; });
    echo json_encode(array('total' => count($rooms), 'rooms' => $rooms, 'server_time' => time()));
    break;

case 'admin_cleanup':
    // Force cleanup of stale rooms now
    $removed = cleanupStaleRooms();
    echo json_encode(array('ok' => true, 'removed' => $removed));
    break;

default:
    echo json_encode(array('error' => 'Unknown action: create_room, join_room, send, poll, room_info, admin_list, admin_cleanup'));
}


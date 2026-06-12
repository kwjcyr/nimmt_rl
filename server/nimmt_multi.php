<?php
/**
 * Nimmt! Multi-user Server - PHP 版
 * 用 Session + 文件存储 实现房间管理
 * 长轮询 (Long Polling) 实现实时通信
 *
 * API:
 *   POST /nimmt/api/multi.php?action=create_room   创建房间
 *   POST /nimmt/api/multi.php?action=join_room     加入房间
 *   GET  /nimmt/api/multi.php?action=poll         长轮询拉取消息
 *   POST /nimmt/api/multi.php?action=send         发送消息 (start_game, play_card, pick_row)
 *   GET  /nimmt/api/multi.php?action=room_info    获取房间信息
 */

header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type');

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') exit(0);

// ============================================================
//  Config & Constants
// ============================================================
define('DATA_DIR', sys_get_temp_dir() . '/nimmt_multi/');
define('POLL_TIMEOUT', 25);       // long poll 秒数
define 'POLL_INTERVAL', 1000000); // 微秒

// Game constants
define('TOTAL', 100);
define('NUM_ROWS', 5);
define('MAX_ROW', 6);
define('HAND_SIZE', 10);
define('END_SCORE', 66);

// Ensure data dir exists
@mkdir(DATA_DIR, 0777, true);

function getDataFile($roomId) { return DATA_DIR . 'room_' . preg_replace('/[^a-zA-Z0-9]/', '', $roomId) . '.json'; }

function readRoom($roomId) {
    $f = getDataFile($roomId);
    if (!file_exists($f)) return null;
    return json_decode(file_get_contents($f), true);
}

function writeRoom($roomId, $data) {
    $f = getDataFile($roomId);
    file_put_contents($f, json_encode($data));
}

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
    $best = -1; $md = 1e9;
    foreach ($rows as $r => $row) {
        $t = $row[count($row)-1];
        if ($t < $card && ($card - $t) < $md) { $md = $card - $t; $best = $r; }
    }
    return $best;
}

function aiChooseCard($hand, $rows, $strategy='greedy') {
    if ($strategy === 'random') return $hand[array_rand($hand)];
    $scored = array();
    foreach ($hand as $card) {
        $br = findBestRow($rows, $card);
        if ($br === -1) {
            $min = PHP_INT_MAX;
            foreach ($rows as $row) $min = min($min, rowBulls($row));
            $risk = $min + 80;
        } elseif (count($rows[$br]) >= MAX_ROW) {
            $risk = rowBulls($rows[$br]) + 40;
        } else {
            $risk = count($rows[$br]) * 3;
        }
        $scored[] = array('card' => $card, 'risk' => $risk + (mt_rand() - 0.5) * 10);
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
    $best = 0; $minB = 1e9;
    foreach ($rows as $r => $row) {
        $b = rowBulls($row);
        if ($b < $minB) { $minB = $b; $best = $r; }
    }
    return $best;
}

function deal() {
    $deck = range(1, TOTAL);
    shuffle($deck);
    $hands = array();
    for ($i = 0; $i < count($deck)/(HAND_SIZE); $i++) {
        $hands[] = array_slice($deck, $i*HAND_SIZE, HAND_SIZE);
        sort($hands[$i]);
    }
    $start = count($hands) * HAND_SIZE;
    $baseCards = array_slice($deck, $start, NUM_ROWS);
    sort($baseCards);
    $rows = array();
    for ($r = 0; $r < NUM_ROWS; $r++) $rows[] = array($baseCards[$r]);
    // Fill to exactly NUM_PLAYERS hands
    while (count($hands) < 6) $hands[] = array();
    return array('hands' => $hands, 'rows' => $rows);
}


// ============================================================
//  Router
// ============================================================

$action = isset($_REQUEST['action']) ? $_REQUEST['action'] : '';
$input = json_decode(file_get_contents('php://input'), true) ?: $_POST;

switch ($action) {

    // ---- Create Room ----
    case 'create_room':
        $pid = isset($input['player_id']) ? $input['player_id'] : '';
        $pname = isset($input['player_name']) ? $input['player_name'] : ('P'.mt_rand(1,999));
        if (!$pid) { echo json_encode(array('error'=>'player_id required')); exit(); }
        $rid = sprintf('%06d', mt_rand(0,999999));
        $room = array(
            'room_id' => $rid,
            'name' => 'Room '.substr($rid,0,6),
            'max_players' => 6,
            'host_id' => $pid,
            'players' => array(array('player_id'=>$pid,'name'=>$pname,'is_ai'=>false,'score'=>0,'hand'=>array())),
            'phase' => 'waiting',
            'rows' => array(), 'round' => 1,
            'chosen' => array(), 'logs' => array(),
            'messages' => array(),     // for long polling
            'last_update' => microtime(true)
        );
        writeRoom($rid, $room);
        echo json_encode(array(
            'room_id' => $rid, 'name' => $room['name'],
            'players' => array_map(function($p){return array('id'=>$p['player_id'],'name'=>$p['name'],'is_ai'=>$p['is_ai']);}, $room['players']),
            'max_players' => $room['max_players']
        ));
        break;

    // ---- Join Room ----
    case 'join_room':
        $rid = isset($input['room_id']) ? $input['room_id'] : '';
        $pid = isset($input['player_id']) ? $input['player_id'] : '';
        $pname = isset($input['player_name']) ? $input['player_name'] : ('P'.mt_rand(1,999));
        if (!$rid || !$pid) { echo json_encode(array('error'=>'room_id and player_id required')); exit(); }
        $room = readRoom($rid);
        if (!$room || count($room['players']) >= $room['max_players']) { echo json_encode(array('error'=>'Room not found or full')); exit(); }
        $exists = false;
        foreach ($room['players'] as $p) { if ($p['player_id'] === $pid) { $exists = true; break; } }
        if (!$exists) {
            $room['players'][] = array('player_id'=>$pid,'name'=>$pname,'is_ai'=>false,'score'=>0,'hand'=>array());
        }
        $room['last_update'] = microtime(true);
        // Add join message
        $room['messages'][] = array('type'=>'player_joined','player_id'=>$pid,'name'=>$pname,'players'=>array_map(function($p){return array('id'=>$p['player_id'],'name'=>$p['name'],'is_ai'=>$p['is_ai']);}, $room['players']));
        writeRoom($rid, $room);
        echo json_encode(array(
            'room_id' => $rid, 'name' => $room['name'],
            'players' => array_map(function($p){return array('id'=>$p['player_id'],'name'=>$p['name'],'is_ai'=>$p['is_ai']);}, $room['players']),
            'max_players' => $room['max_players'], 'phase' => $room['phase']
        ));
        break;

    // ---- Start Game ----
    case 'send':
        $msgType = isset($input['type']) ? $input['type'] : '';
        $rid = isset($input['room_id']) ? $input['room_id'] : '';
        $pid = isset($input['player_id']) ? $input['player_id'] : '';
        $room = readRoom($rid);
        if (!$room) { echo json_encode(array('error'=>'Room not found')); exit(); }

        if ($msgType === 'start_game') {
            if ($room['host_id'] !== $pid) { echo json_encode(array('error'=>'Only host can start')); exit(); }
            $d = deal();
            $room['rows'] = $d['rows'];
            $room['round'] = 1;
            $room['chosen'] = array();
            $room['phase'] = 'playing';
            $room['logs'] = array();
            for ($i = 0; $i < count($room['players']) && $i < count($d['hands']); $i++) {
                $room['players'][$i]['hand'] = $d['hands'][$i];
                $room['players'][$i]['score'] = 0;
            }
            // Send game_start to all
            foreach ($room['players'] as $idx => $p) {
                $room['messages'][] = array(
                    'type' => 'game_start', 'target_pid' => $p['player_id'],
                    'hand' => $p['hand'], 'rows' => $d['rows'], 'round' => 1,
                    'players' => array_map(function($pp){return array('id'=>$pp['player_id'],'name'=>$pp['name'],'score'=>$pp['score'],'is_ai'=>$pp['is_ai']);}, $room['players'])
                );
            }
            $room['messages'][] = array('type'=>'game_started','players'=>array_map(function($p){return array('id'=>$p['player_id'],'name'=>$p['name'],'is_ai'=>$p['is_ai']);}, $room['players']));
            $room['last_update'] = microtime(true);
            writeRoom($rid, $room);
            echo json_encode(array('ok'=>true));
        }

        elseif ($msgType === 'play_card') {
            $card = isset($input['card']) ? intval($input['card']) : 0;
            if ($room['phase'] !== 'playing' || isset($room['chosen'][$pid])) { echo json_encode(array('ok'=>true)); exit(); }
            $room['chosen'][$pid] = $card;
            // Remove from hand
            foreach ($room['players'] as &$p) {
                if ($p['player_id'] === $pid) {
                    $k = array_search($card, $p['hand']);
                    if ($k !== false) array_splice($p['hand'], $k, 1);
                }
            }
            unset($p);
            $realIds = array();
            foreach ($room['players'] as $p) { if (!$p['is_ai']) $realIds[] = $p['player_id']; }
            $remain = count(array_filter($realIds, function($id) use($room){ return !isset($room['chosen'][$id]); }));
            $room['messages'][] = array('type'=>'card_played','player_id'=>$pid,'card'=>$card,'remaining'=>$remain);
            $room['last_update'] = microtime(true);

            // Check if all real players played
            $allPlayed = true;
            foreach ($realIds as $id) { if (!isset($room['chosen'][$id])) { $allPlayed = false; break; } }

            if ($allPlayed) {
                // AI plays
                foreach ($room['players'] as &$p) {
                    if ($p['is_ai'] && !isset($room['chosen'][$p['player_id']]) && !empty($p['hand'])) {
                        $aiCard = aiChooseCard($p['hand'], $room['rows'], 'greedy');
                        $room['chosen'][$p['player_id']] = $aiCard;
                        $k = array_search($aiCard, $p['hand']);
                        if ($k !== false) array_splice($p['hand'], $k, 1);
                    }
                }
                unset($p);
                resolveRound($room, $rid);
            }

            writeRoom($rid, $room);
            echo json_encode(array('ok'=>true));
        }

        elseif ($msgType === 'pick_row') {
            $ri = isset($input['row']) ? intval($input['row']) : 0;
            $card = isset($input['card']) ? intval($input['card']) : 0;
            $penalty = rowBulls($room['rows'][$ri]);
            foreach ($room['players'] as &$p) {
                if ($p['player_id'] === $pid) $p['score'] += $penalty;
            }
            unset($p);
            $room['rows'][$ri] = array($card);
            $room['logs'][] = array('msg'=>$pname.' took row '.($ri+1).' (-'.$penalty.')','cls'=>'penalty');
            $sd = array();
            foreach ($room['players'] as $p) $sd[$p['player_id']] = $p['score'];
            $room['messages'][] = array('type'=>'row_picked','player_id'=>$pid,'row'=>$ri,'rows'=>$room['rows'],'scores'=>$sd);
            $room['last_update'] = microtime(true);
            writeRoom($rid, $room);
            echo json_encode(array('ok'=>true));
        }

        else {
            echo json_encode(array('error'=>'Unknown message type'));
        }
        break;

    // ---- Long Poll ----
    case 'poll':
        $rid = isset($_GET['room_id']) ? $_GET['room_id'] : '';
        $pid = isset($_GET['player_id']) ? $_GET['player_id'] : '';
        $since = isset($_GET['since']) ? floatval($_GET['since']) : 0;
        $room = readRoom($rid);
        if (!$room) { echo json_encode(array('type'=>'error','detail'=>'Room not found')); exit(); }

        // Wait for new messages or timeout
        $start = microtime(true);
        $msgs = array();
        while ((microtime(true) - $start) < POLL_TIMEOUT) {
            $room = readRoom($rid);
            if (!$room) break;
            $newMsgs = array();
            foreach ($room['messages'] as $mi => $m) {
                if ($m['_ts'] > $since) {
                    // Check targeting
                    $targetOk = !isset($m['target_pid']) || $m['target_pid'] === $pid;
                    if ($targetOk) {
                        $newMsgs[] = $m;
                        unset($room['messages'][$mi]); // consume
                    }
                }
            }
            $room['messages'] = array_values($room['messages']);
            writeRoom($rid, $room);
            if (!empty($newMsgs)) {
                $msgs = $newMsgs;
                break;
            }
            usleep(POLL_INTERVAL);
        }
        echo json_encode(array('messages' => $msgs, 'ts' => microtime(true)));
        break;

    // ---- Room Info ----
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
            'logs' => array_slice($room['logs'], 0, 20)
        ));
        break;

    default:
        echo json_encode(array('error'=>'Unknown action. Use: create_room, join_room, poll, send, room_info'));
}


// ============================================================
//  Round Resolution (shared logic)
// ============================================================

function resolveRound(&$room, $rid) {
    $chosen = $room['chosen'];
    uksort($chosen, function($a,$b) use($chosen){ return $chosen[$a] - $chosen[$b]; });

    foreach ($chosen as $pi => $card) {
        $br = findBestRow($room['rows'], $card);
        $po = null;
        foreach ($room['players'] as $p) { if ($p['player_id'] === $pi) { $po = $p; break; } }

        if ($br === -1) {
            if ($po && !$po['is_ai']) {
                $room['messages'][] = array('type'=>'must_pick_row','target_pid'=>$pi,'card'=>$card,'rows'=>$room['rows']);
                continue;
            } else {
                $r = aiChooseRow($room['rows']);
            }
        } elseif (count($room['rows'][$br]) >= MAX_ROW) {
            $r = $br;
        } else {
            $room['rows'][$br][] = $card;
            if ($po) $room['logs'][] = array('msg'=>$po['name'].' played '.$card.' -> row '.($br+1),'cls'=>'');
            continue;
        }
        $penalty = rowBulls($room['rows'][$r]);
        $room['rows'][$r] = array($card);
        if ($po) { $po['score'] += $penalty; $cls = $penalty > 0 ? 'penalty' : ''; $room['logs'][] = array('msg'=>$po['name'].' took row '.($r+1).' (-'.$penalty.')','cls'=>$cls); }
    }

    $room['round']++;
    $room['chosen'] = array();

    $handsEmpty = true;
    $someoneMaxed = false;
    foreach ($room['players'] as $p) {
        if (!$p['is_ai'] && !empty($p['hand'])) $handsEmpty = false;
        if ($p['score'] >= END_SCORE) $someoneMaxed = true;
    }

    if ($handsEmpty || $someoneMaxed) {
        $room['phase'] = 'finished';
        usort($room['players'], function($a,$b){ return $a['score'] - $b['score']; });
        $ranking = array();
        foreach ($room['players'] as $p) $ranking[] = array('name'=>$p['name'],'score'=>$p['score'],'isYou'=>false);
        $room['messages'][] = array('type'=>'game_over','ranking'=>$ranking,'logs'=>array_slice($room['logs'],0,20));
    } else {
        $sd = array();
        foreach ($room['players'] as $p) $sd[$p['player_id']] = $p['score'];
        $room['messages'][] = array('type'=>'round_end','round'=>$room['round'],'rows'=>$room['rows'],'scores'=>$sd,'logs'=>array_slice($room['logs'],0,20));
    }
    $room['last_update'] = microtime(true);
}


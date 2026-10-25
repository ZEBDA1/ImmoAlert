<?php
declare(strict_types=1);

// Same-origin gateway for Plesk shared hosting. No bot credential lives here.
ini_set('display_errors', '0');
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: private, no-store');
header('X-Content-Type-Options: nosniff');
header('X-Frame-Options: DENY');
header('Referrer-Policy: no-referrer');

function fail_request(int $status, string $message): never {
    http_response_code($status);
    echo json_encode(['error' => $message], JSON_UNESCAPED_UNICODE);
    exit;
}

$file = dirname(__DIR__, 2) . '/private/immoalert.php';
if (!is_file($file) || !extension_loaded('curl')) {
    fail_request(503, 'Passerelle non configurée. Contactez l’administrateur.');
}
$config = require $file;
if (!is_array($config)) {
    fail_request(503, 'Configuration de la passerelle invalide.');
}
$backend = rtrim((string)($config['backend_url'] ?? ''), '/');
$public = rtrim((string)($config['public_origin'] ?? ''), '/');
$secret = (string)($config['shared_secret'] ?? '');
foreach ([$backend, $public] as $origin) {
    $parts = parse_url($origin);
    if (!$parts || ($parts['scheme'] ?? '') !== 'https' || empty($parts['host']) ||
        isset($parts['user']) || isset($parts['pass']) || !empty($parts['path']) ||
        isset($parts['query']) || isset($parts['fragment'])) {
        fail_request(503, 'Une adresse HTTPS valide est requise pour le site et le backend.');
    }
}
if (strlen($secret) < 32 || str_contains($secret, 'REMPLACER')) {
    fail_request(503, 'Le secret de liaison doit être configuré.');
}
$method = $_SERVER['REQUEST_METHOD'] ?? '';
$route = $_GET['route'] ?? '';
if (!is_string($route) || strlen($route) > 180) {
    fail_request(404, 'Route inconnue.');
}
$read = preg_match('~^/api/(?:state|health|admin/live|listings/[1-9][0-9]{0,9})$~D', $route) === 1;
$write = preg_match('~^/api/(?:login|logout|action|admin/accounts(?:/[1-9][0-9]{0,9})?)$~D', $route) === 1;
if (!(($method === 'GET' && $read) || ($method === 'POST' && $write))) {
    fail_request(404, 'Route inconnue.');
}
if ($method === 'POST' && ($_SERVER['HTTP_ORIGIN'] ?? '') !== $public) {
    fail_request(403, 'Origine refusée.');
}
if ((int)($_SERVER['CONTENT_LENGTH'] ?? 0) > 8192) {
    fail_request(413, 'Requête trop volumineuse.');
}
$body = file_get_contents('php://input', false, null, 0, 8193);
if ($body === false || strlen($body) > 8192) {
    fail_request(413, 'Requête trop volumineuse.');
}
$stamp = (string)time();
$nonce = bin2hex(random_bytes(16));
$client = (string)($_SERVER['REMOTE_ADDR'] ?? '');
$signed = implode("\n", [$method, $route, $stamp, $nonce, hash('sha256', $body), $public, $client]);
$signature = hash_hmac('sha256', $signed, $secret);
$headers = [
    'Accept: application/json', 'Content-Type: application/json', 'Origin: ' . $public,
    'X-Immo-Public-Origin: ' . $public, 'X-Immo-Timestamp: ' . $stamp,
    'X-Immo-Nonce: ' . $nonce, 'X-Immo-Client-IP: ' . $client,
    'X-Immo-Signature: ' . $signature,
];
$cookie = $_COOKIE['immo_session'] ?? '';
if (is_string($cookie) && preg_match('/^[a-f0-9]{64}$/D', $cookie)) {
    $headers[] = 'Cookie: immo_session=' . $cookie;
}
$cookies = [];
$handle = curl_init($backend . $route);
curl_setopt_array($handle, [
    CURLOPT_CUSTOMREQUEST => $method, CURLOPT_HTTPHEADER => $headers,
    CURLOPT_RETURNTRANSFER => true, CURLOPT_CONNECTTIMEOUT => 5, CURLOPT_TIMEOUT => 25,
    CURLOPT_FOLLOWLOCATION => false, CURLOPT_SSL_VERIFYPEER => true, CURLOPT_SSL_VERIFYHOST => 2,
    CURLOPT_PROTOCOLS => CURLPROTO_HTTPS,
    CURLOPT_HEADERFUNCTION => static function ($curl, string $line) use (&$cookies): int {
        if (stripos($line, 'Set-Cookie: immo_session=') === 0) {
            $value = trim(substr($line, strlen('Set-Cookie: ')));
            if (!str_contains($value, "\r") && !str_contains($value, "\n")) {
                $cookies[] = $value;
            }
        }
        return strlen($line);
    },
]);
if ($method === 'POST') {
    curl_setopt($handle, CURLOPT_POSTFIELDS, $body);
}
$response = curl_exec($handle);
$status = (int)curl_getinfo($handle, CURLINFO_RESPONSE_CODE);
$failed = curl_errno($handle) !== 0;
curl_close($handle);
if ($failed || $response === false || $status < 200 || ($status >= 300 && $status < 400) || $status > 599) {
    fail_request(502, 'Le backend est momentanément inaccessible. Réessayez dans quelques instants.');
}
json_decode($response);
if (json_last_error() !== JSON_ERROR_NONE) {
    fail_request(502, 'Réponse inattendue du backend.');
}
http_response_code($status);
foreach ($cookies as $value) {
    header('Set-Cookie: ' . $value, false);
}
echo $response;

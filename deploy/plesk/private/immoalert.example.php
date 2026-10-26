<?php
// Rename to immoalert.php. Keep this file outside the public httpdocs folder.
return [
    'backend_url' => 'https://backend.example.com',
    'public_origin' => 'https://immo.example.com',
    // Set the same random secret as WEB_PROXY_SECRET (at least 32 characters).
    'shared_secret' => '',
];

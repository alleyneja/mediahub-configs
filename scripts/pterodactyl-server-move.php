<?php
// Drives the panel's own services for one server (used for the Phase 4 Minecraft move, 2026-09-21).
// Run as the panel user ON the panel host: sudo -u www-data php pterodactyl-server-move.php status|stop|start|transfer <server_id> [node_id allocation_id]
// Procedure per server: stop, wait for exit, tar backup of /srv/pterodactyl/<uuid>, transfer, start, ping. Pterodactyl deletes the source files after a successful transfer.
require '/var/www/pterodactyl/vendor/autoload.php';
$app = require '/var/www/pterodactyl/bootstrap/app.php';
$app->make(Illuminate\Contracts\Console\Kernel::class)->bootstrap();
use Pterodactyl\Models\Server;
[$_, $action, $id] = $argv; $server = Server::findOrFail((int)$id);
try {
  switch ($action) {
    case 'status':
      echo json_encode(['id'=>$server->id,'name'=>$server->name,'node_id'=>$server->node_id,'alloc'=>$server->allocation->port,'alloc_id'=>$server->allocation_id,'status'=>$server->status])."\n"; break;
    case 'stop': case 'start':
      $app->make(Pterodactyl\Repositories\Wings\DaemonPowerRepository::class)->setServer($server)->send($action); echo "$action sent\n"; break;
    case 'transfer':
      $req = Illuminate\Http\Request::create('/x','POST',['node_id'=>(int)$argv[3],'allocation_id'=>(int)$argv[4]]);
      $req->setLaravelSession($app['session.store']);
      $app->make(Pterodactyl\Http\Controllers\Admin\Servers\ServerTransferController::class)->transfer($req, $server); echo "transfer started\n"; break;
  }
} catch (\Throwable $e) { echo "ERROR ".get_class($e).": ".substr($e->getMessage(),0,300)."\n"; exit(1); }

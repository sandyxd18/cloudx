# Deploy the working tree's services/ to the lab (controller-node, compute-node, storage-node over ssh).
#   scripts\deploy-lab.ps1                 services + shared + migrations on all nodes
#   scripts\deploy-lab.ps1 -Dashboard      also build the frontend and swap it in on the controller
# Workers only get the units that are already active (the controller-only APIs stay disabled there).
param([switch]$Dashboard, [string[]]$Nodes = @('controller-node', 'compute-node', 'storage-node'))
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$tmp = $env:TEMP

Push-Location (Join-Path $root 'services')
tar --exclude=node_modules --exclude=__pycache__ --exclude=frontend --exclude=static -czf "$tmp\svc.tgz" .
Pop-Location

if ($Dashboard) {
    Push-Location (Join-Path $root 'services\dashboard\frontend')
    npm.cmd run build
    if ($LASTEXITCODE) { throw 'frontend build failed' }
    tar -czf "$tmp\dist.tgz" -C dist .
    git checkout -- package-lock.json 2>$null   # npm rewrites it
    Pop-Location
}

# ponytail: restarts every active cloudx unit; add a -Service filter if that gets too disruptive.
$remote = @'
set -e
cd /opt/cloudx/services && sudo tar -xzf /tmp/svc.tgz
sudo find /opt/cloudx/services -name '*.sh' -exec chmod +x {} +   # tar from Windows drops the exec bit
sudo chown -R cloudx:cloudx /opt/cloudx/services
SP=/opt/cloudx/venv/lib/python3.12/site-packages
sudo rm -rf $SP/shared && sudo cp -r /opt/cloudx/services/shared $SP/shared && sudo chown -R cloudx:cloudx $SP/shared
cd /tmp   # cloudx-db-migrate resolves the services dir from here, never from inside /opt/cloudx/services
if systemctl is-active -q cloudx-auth-api; then sudo -u cloudx /opt/cloudx/venv/bin/cloudx-db-migrate 2>&1 | grep -E '^(==>|!!)'; fi
if [ "$1" = dash ]; then
  D=/opt/cloudx/services/dashboard/app
  sudo rm -rf $D/static.new && sudo mkdir $D/static.new && sudo tar -xzf /tmp/dist.tgz -C $D/static.new
  sudo rm -rf $D/static.old && sudo mv $D/static $D/static.old && sudo mv $D/static.new $D/static
  sudo chown -R cloudx:cloudx $D/static && sudo rm -rf $D/static.old
fi
sudo systemctl restart $(systemctl list-units 'cloudx-*' --no-legend --state=active | sed 's/^[^a-z]*//' | cut -d' ' -f1)
sleep 8
echo "failed: $(systemctl list-units 'cloudx-*' --no-legend --state=failed,activating | wc -l)"
'@
$script = "$tmp\deploy-lab.sh"
[IO.File]::WriteAllText($script, ($remote -replace "`r", ''))

foreach ($n in $Nodes) {
    Write-Host "== $n"
    $dash = if ($Dashboard -and $n -eq 'controller-node') { 'dash' } else { 'no' }
    scp "$tmp\svc.tgz" "${n}:/tmp/svc.tgz"; scp $script "${n}:/tmp/deploy-lab.sh"
    if ($dash -eq 'dash') { scp "$tmp\dist.tgz" "${n}:/tmp/dist.tgz" }
    ssh $n "bash /tmp/deploy-lab.sh $dash > /tmp/deploy-lab.out 2>&1; cat /tmp/deploy-lab.out"
}

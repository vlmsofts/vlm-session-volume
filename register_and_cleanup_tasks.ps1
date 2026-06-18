# ============================================================================
# VLM Session-Volume — register corrected job + find/clean stale tasks
# RUN IN AN ELEVATED PowerShell (Run as Administrator).
# Work top to bottom: review STEP 1, then run STEP 2, then STEP 3.
# ============================================================================
$dir = "C:\Users\Louis\OneDrive - VLM Commodities LTD\Desktop\VLM_Session_Volume_Project"

# ---- STEP 1: DISCOVER — what is actually registered? (read-only) -----------
Write-Host "`n=== Scheduled tasks related to session-volume / price-tape ===" -ForegroundColor Cyan
Get-ScheduledTask | Where-Object {
    $_.TaskName -match 'Session.?Volume|FutVol|PriceTape|CottonPriceTape|overnight'
} | ForEach-Object {
    $a = ($_.Actions | Where-Object {$_.Execute} | Select-Object -First 1)
    [pscustomobject]@{
        TaskName  = $_.TaskName
        State     = $_.State
        Runs      = ($a.Arguments)
        WorkDir   = ($a.WorkingDirectory)
    }
} | Format-Table -AutoSize -Wrap

# ---- STEP 2: REGISTER the corrected EOD job (futures engine, new folder) ----
# Safe / idempotent (-Force overwrites if it already exists).
Register-ScheduledTask -TaskName "VLM_Session_Volume_EOD" `
    -Xml (Get-Content "$dir\VLM_Session_Volume_EOD.xml" -Raw) -Force
Write-Host "Registered VLM_Session_Volume_EOD (14:35 ET, futures engine)." -ForegroundColor Green

# Morning job is an interim no-op until the 14:20 boundary exists — leave OFF
# unless you want it. To enable, uncomment:
# Register-ScheduledTask -TaskName "VLM_Session_Volume_Morning" `
#     -Xml (Get-Content "$dir\VLM_Session_Volume_Morning.xml" -Raw) -Force

# ---- STEP 3: REMOVE STALE tasks — review STEP 1 output FIRST ----------------
# Uncomment ONLY the names STEP 1 showed that run the OPTIONS engine
# (Runs contains "\session_volume.py") or the OLD "...\vlm_session_volume\" path.
# DO NOT remove VLM_CT_PriceTape_Start (that is your live capture).
#
# Unregister-ScheduledTask -TaskName "VLM Session Volume Morning" -Confirm:$false
# Unregister-ScheduledTask -TaskName "VLM Session Volume EOD"     -Confirm:$false
# Unregister-ScheduledTask -TaskName "VLM_CT_FutVol_EOD"          -Confirm:$false
# Unregister-ScheduledTask -TaskName "VLM_CT_FutVol_SeedRefresh"  -Confirm:$false
# Unregister-ScheduledTask -TaskName "CottonPriceTape"            -Confirm:$false  # only if VLM_CT_PriceTape_Start exists & is the live one

Write-Host "`nDone. Re-run STEP 1 to confirm the final task list." -ForegroundColor Cyan

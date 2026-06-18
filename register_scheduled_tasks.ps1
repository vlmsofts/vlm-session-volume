# Register the consolidated futures session-volume tasks.
# RUN IN AN ELEVATED PowerShell (Run as Administrator) -- the tasks use RunLevel=HighestAvailable.
# Re-registers (overwrites) the old buggy tasks that pointed at the options engine.

$dir = "C:\Users\Louis\OneDrive - VLM Commodities LTD\Desktop\VLM_Session_Volume_Project"

Register-ScheduledTask -TaskName "VLM_Session_Volume_Morning" `
    -Xml (Get-Content "$dir\VLM_Session_Volume_Morning.xml" -Raw) -Force

Register-ScheduledTask -TaskName "VLM_Session_Volume_EOD" `
    -Xml (Get-Content "$dir\VLM_Session_Volume_EOD.xml" -Raw) -Force

Write-Host "Registered VLM_Session_Volume_Morning (07:15) and VLM_Session_Volume_EOD (14:35)."

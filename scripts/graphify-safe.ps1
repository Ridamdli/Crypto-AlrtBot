param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GraphifyArgs)
# Graphify wrapper: forces GRAPHIFY_BACKEND=none so doc/PDF semantic extraction never
# bills this repo (GEMINI_API_KEY is set globally). Use this instead of raw `graphify`.
# shortcut: pass -UseLlm to opt in deliberately, when a paid semantic pass is approved.
if ($GraphifyArgs -contains "-UseLlm") {
    $GraphifyArgs = $GraphifyArgs | Where-Object { $_ -ne "-UseLlm" }
    Remove-Item Env:GRAPHIFY_BACKEND -ErrorAction SilentlyContinue
} else {
    $env:GRAPHIFY_BACKEND = "none"
}
& graphify @GraphifyArgs
exit $LASTEXITCODE

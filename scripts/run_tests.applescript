-- Runs scripts/run_tests.sh. Pass repo root as argv[1] (recommended from CLI).
-- Example:  osascript ./scripts/run_tests.applescript "$PWD"
-- Or use:   ./scripts/run_tests_via_applescript.sh
--
-- If argv is empty, falls back to path to me (may fail if path to me is wrong).

on run argv
	set repoRoot to ""
	if (count of argv) > 0 then
		set repoRoot to item 1 of argv
	else
		try
			set scriptPath to POSIX path of (path to me as alias)
			set repoRoot to do shell script "cd $(dirname " & quoted form of scriptPath & ")/.. && pwd"
		on error
			display dialog "Pass repo root: osascript scripts/run_tests.applescript \"$PWD\"" buttons {"OK"} default button 1 with icon stop
			return
		end try
	end if
	set bashCmd to "cd " & quoted form of repoRoot & " && ./scripts/run_tests.sh"
	try
		do shell script bashCmd
	on error errMsg number errNum
		display dialog "Tests failed (" & errNum & "): " & errMsg buttons {"OK"} default button 1 with icon stop
		error number errNum
	end try
	display dialog "All checks passed." buttons {"OK"} default button 1 with icon note
end run

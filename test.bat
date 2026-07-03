@echo off
setlocal EnableDelayedExpansion
(
    choice /C yn /T 1 /D n /M "Press Y or N"
    if errorlevel 2 (
        echo NO
    ) else (
        echo YES
    )
)

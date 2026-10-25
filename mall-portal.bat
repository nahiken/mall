@echo off
chcp 65001 >nul
rem ===== Start mall-portal (:8085) =====
rem Requires JDK 17+ and Maven on PATH.
if "%JAVA_HOME%"=="" (
  echo [ERROR] JAVA_HOME is not set. Point it to a JDK 17+ installation.
  pause
  exit /b 1
)
set "PATH=%JAVA_HOME%\bin;%PATH%"
cd /d "%~dp0mall-master\mall-portal"
call mvn spring-boot:run

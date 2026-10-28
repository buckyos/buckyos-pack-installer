#!/bin/zsh
# Shared mode selection; sourced by pkg hooks and embedded in macos.sh.
desktop_profile_edition() {
  /usr/bin/perl -MJSON::PP -e '
    local $/; open my $f, "<", $ARGV[0] or die "Cannot read installed product profile\n";
    -s $ARGV[0] <= 1048576 or die "Oversized product profile\n";
    my $p = decode_json(<$f>); my $c = $p->{capabilities};
    die "Invalid product profile\n" unless JSON::PP::encode_json($p->{schema_version}) eq "1" &&
      $p->{edition} =~ /\A(full|lite)\z/ && ref($c) eq "HASH" &&
      JSON::PP::is_bool($c->{container}) && JSON::PP::is_bool($c->{user_service_apps}) && JSON::PP::is_bool($c->{static_web_apps});
    die "Invalid Lite capabilities\n" if $p->{edition} eq "lite" &&
      ($c->{container} || $c->{user_service_apps} || ($c->{max_running_agents} // 0) != 1);
    die "Invalid Full capabilities\n" if $p->{edition} eq "full" &&
      (!$c->{container} || !$c->{user_service_apps} || !$c->{static_web_apps} || defined $c->{max_running_agents});
    print $p->{edition};
  ' "$1"
}

desktop_user() {
  local user
  user="$(/usr/bin/stat -f%Su /dev/console 2>/dev/null || true)"
  case "$user" in ''|root|loginwindow|_mbsetupuser) return 1 ;; esac
  print -r -- "$user"
}

desktop_as_user() {
  local user uid user_home
  user="$(desktop_user)" || return 1
  uid="$(/usr/bin/id -u "$user")" || return 1
  user_home="$(/usr/bin/dscl . -read "/Users/$user" NFSHomeDirectory | /usr/bin/sed 's/^NFSHomeDirectory: //')" || return 1
  /bin/launchctl asuser "$uid" /usr/bin/sudo -H -u "$user" /usr/bin/env \
    PATH="$user_home/.docker/bin:$user_home/.orbstack/bin:/opt/homebrew/bin:/usr/local/bin:/Applications/Docker.app/Contents/Resources/bin:/usr/bin:/bin:/usr/sbin:/sbin" "$@"
}

desktop_docker_ready() {
  local info
  info="$(/usr/bin/perl -e '$t=shift; alarm $t; exec @ARGV' 20 \
    /bin/zsh -c "$(functions desktop_user desktop_as_user); desktop_as_user docker info --format '{{.OSType}}'" 2>/dev/null)" || return 1
  [[ "$info" == linux ]]
}

desktop_confirm_lite() {
  local answer user
  local message='Lite disables containers and user service apps, supports static web and bundled script apps, and runs at most one Agent. Install Lite?'
  if [[ -t 0 ]] || [[ -e /dev/tty && "${BUCKYOS_ONLINE_INSTALL:-0}" == 1 ]]; then
    print -r -- "$message [y/N]" >/dev/tty || return 1
    IFS= read -r answer </dev/tty || return 1
    [[ "$answer" == [yY] || "$answer" == [yY][eE][sS] ]]
  else
    user="$(desktop_user)" || return 1
    desktop_as_user /usr/bin/osascript -e 'display dialog "'"$message"'" buttons {"Cancel", "Install Lite"} default button "Cancel" with title "BuckyOS"' >/dev/null 2>&1
  fi
}

desktop_select_mode() {
  local root="$1" silent="$2" lite="$3" stage="$4" old=''
  if [[ -L "$root/etc/product.json" ]]; then print -u2 'Installed profile must not be a link.'; return 1; fi
  if [[ -f "$root/etc/product.json" ]]; then
    old="$(desktop_profile_edition "$root/etc/product.json")" || return 1
  elif [[ -e "$root/bin/node-daemon" || ( -f "$root/etc/node_identity.json" && ! -f "$root/.online-uninstalled.json" ) || -f "$root/.online-installed-version" ]]; then old=full
  fi
  if [[ "$old" == full && "$lite" == 1 ]]; then print -u2 '--lite conflicts with existing Full; mode conversion is not supported.'; return 1; fi
  if [[ "$old" == lite || "$lite" == 1 ]]; then print lite; return; fi
  if [[ "$stage" == 1 ]]; then print full; return; fi
  if desktop_docker_ready; then print full; return; fi
  print -u2 'A Docker Linux engine accessible to the desktop user is unavailable. Automatic Docker setup on macOS is not supported.'
  if [[ "$silent" == 1 || "$old" == full ]]; then return 1; fi
  desktop_confirm_lite || return 1
  print lite
}

if [[ "${ZSH_EVAL_CONTEXT}" == toplevel ]]; then
  desktop_select_mode "$@"
fi

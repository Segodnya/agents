# Shellchecks the script blocks of GitLab CI jobs, for config repos with no checks of their own.
#
#   ruby ci_shellcheck.rb [--severity style|info|warning|error] <file.yml>...
#
# before_script + script share one shell, after_script runs in its own — checked apart.
# Parsed as POSIX sh: job images like curlimages/curl ship busybox ash, not bash.
# SC2154 is off: CI variables come from `variables:` and the runner, not from the script.
# Exit 0 clean, 1 findings, 2 usage.
require "yaml"
require "open3"

severity = "style"
if (i = ARGV.index("--severity"))
  severity = ARGV.delete_at(i + 1)
  ARGV.delete_at(i)
end
if ARGV.empty? || severity.nil?
  warn "usage: ci_shellcheck.rb [--severity LEVEL] <file.yml>..."
  exit 2
end

failed = false
ARGV.each do |path|
  # CI yml leans on anchors; Psych 4 (Ruby 3.1+) refuses them in load_file, system Ruby 2.6 has no unsafe_*
  doc = YAML.respond_to?(:unsafe_load_file) ? YAML.unsafe_load_file(path) : YAML.load_file(path)
  doc.each do |job, cfg|
    next unless cfg.is_a?(Hash)
    { "script" => %w[before_script script], "after_script" => %w[after_script] }.each do |name, keys|
      lines = keys.flat_map { |k| Array(cfg[k]).flatten }
      next if lines.empty?
      out, st = Open3.capture2e("shellcheck", "-s", "sh", "-S", severity, "-e", "SC2154", "-",
                                stdin_data: lines.join("\n") + "\n")
      next if st.success?
      failed = true
      puts "#{path} #{job}.#{name}:", out
    end
  end
end
exit(failed ? 1 : 0)

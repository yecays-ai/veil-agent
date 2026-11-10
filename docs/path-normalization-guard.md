Normalize Windows paths by guarding on the exact extended-length prefix \\?\ and returning those paths unchanged; a blanket backslash-to-slash pass would still mangle device paths like \\.\pipe\name.

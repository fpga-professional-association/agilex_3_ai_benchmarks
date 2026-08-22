package require -exact qsys 26.1
set all [lsort [info commands]]
puts "TOTAL [llength $all]"
foreach c $all {
    if {[string match "*instance*" $c] || [string match "*reload*" $c]
        || [string match "*refresh*" $c] || [string match "*upgrade*" $c]
        || [string match "*component*" $c] || [string match "*validat*" $c]} {
        puts "CMD $c"
    }
}

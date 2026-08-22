package require -exact qsys 26.1
foreach c [get_connections] {
  if {[string equal [get_connection_property $c TYPE] "avalon"]} {
    set b [get_connection_parameter_value $c baseAddress]
    puts "MAP $c base=$b"
  }
}
foreach i [get_instances] {
  foreach f [get_instance_interfaces $i] {
    set span ""
    catch { set span [get_instance_interface_property $i $f addressSpan] }
    if {[string length $span] > 0} { puts "SPAN $i.$f = $span" }
  }
}

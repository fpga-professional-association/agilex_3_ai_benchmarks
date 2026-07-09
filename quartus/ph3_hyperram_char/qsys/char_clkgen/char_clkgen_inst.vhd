	component char_clkgen is
		port (
			clk_ref_clk        : in  std_logic := 'X'; -- clk
			clk_clk            : out std_logic;        -- clk
			clk2x_clk          : out std_logic;        -- clk
			locked_export      : out std_logic;        -- export
			reset_in_reset     : in  std_logic := 'X'; -- reset
			fabric_reset_reset : out std_logic         -- reset
		);
	end component char_clkgen;

	u0 : component char_clkgen
		port map (
			clk_ref_clk        => CONNECTED_TO_clk_ref_clk,        --      clk_ref.clk
			clk_clk            => CONNECTED_TO_clk_clk,            --          clk.clk
			clk2x_clk          => CONNECTED_TO_clk2x_clk,          --        clk2x.clk
			locked_export      => CONNECTED_TO_locked_export,      --       locked.export
			reset_in_reset     => CONNECTED_TO_reset_in_reset,     --     reset_in.reset
			fabric_reset_reset => CONNECTED_TO_fabric_reset_reset  -- fabric_reset.reset
		);


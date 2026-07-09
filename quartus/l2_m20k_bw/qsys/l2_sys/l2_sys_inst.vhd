	component l2_sys is
		port (
			clk_25_clk           : in  std_logic                     := 'X';             -- clk
			clk_clk              : out std_logic;                                        -- clk
			locked_export        : out std_logic;                                        -- export
			master_address       : out std_logic_vector(31 downto 0);                    -- address
			master_readdata      : in  std_logic_vector(31 downto 0) := (others => 'X'); -- readdata
			master_read          : out std_logic;                                        -- read
			master_write         : out std_logic;                                        -- write
			master_writedata     : out std_logic_vector(31 downto 0);                    -- writedata
			master_waitrequest   : in  std_logic                     := 'X';             -- waitrequest
			master_readdatavalid : in  std_logic                     := 'X';             -- readdatavalid
			master_byteenable    : out std_logic_vector(3 downto 0);                     -- byteenable
			reset_reset          : in  std_logic                     := 'X';             -- reset
			sys_reset_reset      : out std_logic                                         -- reset
		);
	end component l2_sys;

	u0 : component l2_sys
		port map (
			clk_25_clk           => CONNECTED_TO_clk_25_clk,           --    clk_25.clk
			clk_clk              => CONNECTED_TO_clk_clk,              --       clk.clk
			locked_export        => CONNECTED_TO_locked_export,        --    locked.export
			master_address       => CONNECTED_TO_master_address,       --    master.address
			master_readdata      => CONNECTED_TO_master_readdata,      --          .readdata
			master_read          => CONNECTED_TO_master_read,          --          .read
			master_write         => CONNECTED_TO_master_write,         --          .write
			master_writedata     => CONNECTED_TO_master_writedata,     --          .writedata
			master_waitrequest   => CONNECTED_TO_master_waitrequest,   --          .waitrequest
			master_readdatavalid => CONNECTED_TO_master_readdatavalid, --          .readdatavalid
			master_byteenable    => CONNECTED_TO_master_byteenable,    --          .byteenable
			reset_reset          => CONNECTED_TO_reset_reset,          --     reset.reset
			sys_reset_reset      => CONNECTED_TO_sys_reset_reset       -- sys_reset.reset
		);


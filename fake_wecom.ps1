param([int]$x0,[int]$y0,[int]$w,[int]$h,
      [int]$trx,[int]$tryy,[int]$trw,[int]$trh,
      [int]$crx,[int]$cry,[int]$crw)
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$f = New-Object System.Windows.Forms.Form
$f.Text = "企业微信"
$f.StartPosition = "Manual"
$f.Location = New-Object System.Drawing.Point($x0,$y0)
$f.Size = New-Object System.Drawing.Size($w,$h)
$f.FormBorderStyle = "None"
$f.TopMost = $true
$f.BackColor = [System.Drawing.Color]::White
$font = New-Object System.Drawing.Font("Microsoft YaHei", 14)
$tl = New-Object System.Windows.Forms.Label
$tl.Text = "测试客服会话"
$tl.Font = $font
$tl.Location = New-Object System.Drawing.Point($trx,$tryy)
$tl.Size = New-Object System.Drawing.Size($trw,($trh+24))
$tl.BackColor = [System.Drawing.Color]::LightYellow
$f.Controls.Add($tl)
$c1 = New-Object System.Windows.Forms.Label
$c1.Text = "客户：充电宝丢了怎么办"
$c1.Font = $font
$c1.AutoSize = $true
$c1.Location = New-Object System.Drawing.Point(($crx+10),($cry+16))
$f.Controls.Add($c1)
$c2 = New-Object System.Windows.Forms.Label
$c2.Text = "客服：别急，我帮您查一下"
$c2.Font = $font
$c2.AutoSize = $true
$c2.Location = New-Object System.Drawing.Point(($crx+[int]($crw*0.62)),($cry+72))
$f.Controls.Add($c2)
$f.Add_Shown({ $f.Activate() })
[void]$f.ShowDialog()

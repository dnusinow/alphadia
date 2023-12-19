import * as React from 'react'
import styled from '@emotion/styled'

import { Box, Checkbox, FormControl, MenuItem, Select, Stack, Tooltip, Typography, TextField } from '@mui/material'

const StyledCheckbox = styled(Checkbox)(({ theme }) => ({
    padding: 0.5
}))

const ParameterInput = ({
        parameter,
        onChange = () => {},
        sx
    }) => {

        let input = null;
        
        switch (parameter.type) {
            case "integer":
                input = (
                    <TextField
                    id="outlined-number"
                    type="number"  
                    variant="standard"
                    size="small"
                    sx = {{width: "150px"}}
                    value={parameter.value}
                    onChange={(event) => {onChange(parseInt(event.target.value))}}
                    inputProps={{step: "1", lang:"en-US"}}
                />)
                break;
            case "float":
                input = (
                    <TextField
                    id="outlined-number"
                    type="number"  
                    variant="standard"
                    size="small"
                    sx = {{width: "150px"}}
                    value={parameter.value}
                    onChange={(event) => {onChange(parseFloat(event.target.value))}}
                    inputProps={{step: "0.01", lang:"en-US"}}
                />)
                break;
            case "string":
                input = (
                    <TextField
                    id="outlined-number"
                    type="text"
                    variant="standard"
                    size="small"
                    sx = {{width: "150px"}}
                    value={parameter.value}
                    onChange={(event) => {onChange(event.target.value)}}
                />)
                break;
            case "boolean":
                input = (
                    <Box sx={{width: "150px"}}>
                        <StyledCheckbox 
                            checked={parameter.value}
                            size='small'
                            onChange={(event) => {onChange(event.target.checked)}}
                            />
                    </Box>
                )
                break;
            case "dropdown":
                input = (
                    <FormControl variant="standard" size="small" sx={{width: "150px", minHeight: "0px"}}>
                        <Select
                            value={parameter.value}
                            onChange={(event) => {onChange(event.target.value)}}
                            >
                            {parameter.options.map((option) => {
                                return (
                                    <MenuItem value={option}>{option}</MenuItem>
                                )
                            })}
                        </Select>
                    </FormControl>
                )
                break;
            case "integer_range":
                input = (
                    <Box sx={{width: "150px"}}>
                    <TextField
                        id="outlined-number"
                        type="number"  
                        variant="standard"
                        size="small"
                        sx = {{width: "70px"}}
                        value={parameter.value[0]}
                        onChange={(event) => {onChange([parseInt(event.target.value), parameter.value[1]])}}
                        inputProps={{step: "1", lang:"en-US"}}
                    />
                    <TextField
                        id="outlined-number"
                        type="number"  
                        variant="standard"
                        size="small"
                        sx = {{width: "70px", marginLeft: "10px"}}
                        value={parameter.value[1]}
                        onChange={(event) => {onChange([parameter.value[0], parseInt(event.target.value)])}}
                        inputProps={{step: "1", lang:"en-US"}}
                    />
                    </Box>)
                break;

            case "float_range":
                input = (
                    <Box sx={{width: "150px"}}>
                    <TextField
                        id="outlined-number"
                        type="number"  
                        variant="standard"
                        size="small"
                        sx = {{width: "70px"}}
                        value={parameter.value[0]}
                        onChange={(event) => {onChange([parseFloat(event.target.value), parameter.value[1]])}}
                        inputProps={{step: "0.01", lang:"en-US"}}
                    />
                    <TextField
                        id="outlined-number"
                        type="number"  
                        variant="standard"
                        size="small"
                        sx = {{width: "70px", marginLeft: "10px"}}
                        value={parameter.value[1]}
                        onChange={(event) => {onChange([parameter.value[0], parseFloat(event.target.value)])}}
                        inputProps={{step: "0.01", lang:"en-US"}}
                    />
                    </Box>)
                break;

            default:
                input = (
                    <Typography>
                        {parameter.value}
                    </Typography>
                )
        }

    // make Grid which takes 100% of the height
    // The last row should grow to fill the remaining space
    return (
        
            <Stack
            direction="row"
            justifyContent="space-between"
            alignItems="center"
            spacing={2}
            sx={{minHeight: "30px"}}
            >
            <Tooltip title = {parameter.description} disableInteractive>
                <Typography sx={{fontWeight: 400, fontSize: "12px"}}>
                    {parameter.name}
                </Typography>
            </Tooltip>
                {input}
            
            </Stack>
        
        
    )
}

export default ParameterInput



